"""
Talks to a local model served by Ollama (OpenAI-compatible endpoint) and
uses real tool-calling: the model must call `submit_triage` with a
plain-language summary, a reportability recommendation, and its reasoning,
rather than just returning free text we'd have to parse hopefully.

Nothing here ever leaves your machine — this is a plain HTTP call to
localhost.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = 2


class OllamaConnectionError(RuntimeError):
    """Raised when the local Ollama server can't be reached at all."""

SUBMIT_TRIAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_triage",
        "description": (
            "Submit the triage result for one safety event: a plain-language "
            "summary and a reportability recommendation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "2-3 sentence plain-language summary of what happened, "
                        "written for a non-engineer reviewer."
                    ),
                },
                "recommend_reportable": {
                    "type": "boolean",
                    "description": (
                        "True if this event likely needs regulatory reporting "
                        "or human safety review, based on the narrative and "
                        "structured fields together."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": "One sentence explaining the recommendation.",
                },
            },
            "required": ["summary", "recommend_reportable", "reasoning"],
        },
    },
}

SYSTEM_PROMPT = """You are a safety-event triage assistant. You are given a \
structured event (already run through a deterministic rules engine) and its \
free-text narrative. Your jobs:

1. Write a short, plain-language summary of what happened, for someone who \
is not an engineer.
2. Recommend whether the event needs regulatory reporting / human safety \
review, considering the narrative details the rules engine can't see \
(e.g. a near-miss described in the narrative but not captured in structured \
fields).

Important: if the rules engine already flagged this event as reportable, \
you may NOT recommend against reporting it — treat that as a floor, not a \
suggestion. You may only ESCALATE a rules-engine "not reportable" event to \
reportable if the narrative describes something the structured fields miss. \
Always call submit_triage with your result; do not respond with plain text."""


class LLMClient:
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        timeout: int = 120,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _connection_error(self) -> OllamaConnectionError:
        return OllamaConnectionError(
            f"Could not reach Ollama at {self.base_url} — is 'ollama serve' "
            f"running, and is the model pulled? (ollama pull {self.model})"
        )

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.ConnectionError:
            # Connection refused: Ollama isn't running. Retrying just wastes
            # time, so fail fast with an actionable message.
            raise self._connection_error() from None
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "Transient error calling Ollama (%s); retrying once in %ss",
                exc,
                RETRY_BACKOFF_SECONDS,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
            except requests.exceptions.ConnectionError:
                raise self._connection_error() from None

        response.raise_for_status()
        return response.json()

    def triage_event(
        self, event: Dict[str, Any], rule_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        user_content = json.dumps(
            {
                "event_id": event["event_id"],
                "structured_fields": event["fields"],
                "narrative": event["narrative"],
                "rule_severity": rule_result["rule_severity"],
                "rule_reportable": rule_result["rule_reportable"],
                "rule_reasons": rule_result["matched_reasons"],
            },
            default=str,
        )

        # The instructions ride in the user message instead of a system role:
        # some local chat templates (e.g. qwen2.5 on Ollama) stop emitting
        # tool calls entirely when a system message is combined with tools.
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"{SYSTEM_PROMPT}\n\nTriage this safety event and "
                        f"submit your result with the submit_triage tool:\n"
                        f"{user_content}"
                    ),
                },
            ],
            "tools": [SUBMIT_TRIAGE_TOOL],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_triage"},
            },
            "temperature": 0.1,
        }

        data = self._post(payload)

        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            # Model didn't use the tool (some local models are inconsistent
            # about forced tool_choice) — fall back to treating any content
            # as the summary and defer entirely to the rules engine.
            return {
                "summary": message.get("content", "").strip()
                or "(model did not return a structured result)",
                "llm_reportable": rule_result["rule_reportable"],
                "llm_reasoning": "LLM did not call the tool; deferred to rules engine.",
            }

        args = json.loads(tool_calls[0]["function"]["arguments"])
        llm_reportable = bool(args.get("recommend_reportable", False))

        # Enforce the floor: rules-engine reportable can only be escalated,
        # never downgraded, by the LLM.
        final_reportable = rule_result["rule_reportable"] or llm_reportable

        return {
            "summary": args.get("summary", "").strip(),
            "llm_reportable": final_reportable,
            "llm_reasoning": args.get("reasoning", "").strip(),
        }


class StubClient:
    """Used with --no-llm to test the pipeline mechanics without Ollama."""

    def triage_event(
        self, event: Dict[str, Any], rule_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "summary": f"[stub] {event['narrative'][:140]}",
            "llm_reportable": rule_result["rule_reportable"],
            "llm_reasoning": "LLM skipped (--no-llm); rules engine result used as-is.",
        }

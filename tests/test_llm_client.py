"""Tests for triage.llm_client with requests.post mocked — no Ollama needed."""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from triage.llm_client import LLMClient, OllamaConnectionError, StubClient

EVENT = {
    "event_id": "test-1",
    "narrative": "Vehicle made contact with a parked car at low speed.",
    "fields": {"injury_severity": "None", "vehicle_towed": "No"},
}


def make_rule_result(reportable):
    return {
        "rule_severity": "low",
        "rule_reportable": reportable,
        "matched_reasons": [],
    }


def make_response(message):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": message}]}
    return response


def tool_call_message(summary, recommend_reportable, reasoning="because"):
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "function": {
                    "name": "submit_triage",
                    "arguments": json.dumps(
                        {
                            "summary": summary,
                            "recommend_reportable": recommend_reportable,
                            "reasoning": reasoning,
                        }
                    ),
                }
            }
        ],
    }


@patch("triage.llm_client.requests.post")
def test_well_formed_tool_call_parses(mock_post):
    mock_post.return_value = make_response(
        tool_call_message("A minor parking-lot contact.", False, "No injuries.")
    )
    result = LLMClient(model="test-model").triage_event(EVENT, make_rule_result(False))

    assert result == {
        "summary": "A minor parking-lot contact.",
        "llm_reportable": False,
        "llm_reasoning": "No injuries.",
    }
    payload = mock_post.call_args.kwargs["json"]
    assert payload["tool_choice"]["function"]["name"] == "submit_triage"


@patch("triage.llm_client.requests.post")
def test_floor_llm_cannot_downgrade_reportable(mock_post):
    mock_post.return_value = make_response(tool_call_message("Summary.", False))
    result = LLMClient(model="test-model").triage_event(EVENT, make_rule_result(True))
    assert result["llm_reportable"] is True


@patch("triage.llm_client.requests.post")
def test_llm_can_escalate_non_reportable(mock_post):
    mock_post.return_value = make_response(tool_call_message("Summary.", True))
    result = LLMClient(model="test-model").triage_event(EVENT, make_rule_result(False))
    assert result["llm_reportable"] is True


@patch("triage.llm_client.requests.post")
def test_no_tool_call_falls_back_gracefully(mock_post):
    mock_post.return_value = make_response(
        {"role": "assistant", "content": "Just some prose, no tool call."}
    )
    result = LLMClient(model="test-model").triage_event(EVENT, make_rule_result(False))

    assert result["summary"] == "Just some prose, no tool call."
    assert result["llm_reportable"] is False
    assert "deferred to rules engine" in result["llm_reasoning"]


@patch("triage.llm_client.requests.post")
def test_no_tool_call_fallback_keeps_reportable_floor(mock_post):
    mock_post.return_value = make_response({"role": "assistant", "content": ""})
    result = LLMClient(model="test-model").triage_event(EVENT, make_rule_result(True))
    assert result["llm_reportable"] is True
    assert result["summary"] == "(model did not return a structured result)"


@patch("triage.llm_client.requests.post")
def test_connection_error_fails_fast_with_clear_message(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError("refused")
    client = LLMClient(model="test-model", base_url="http://localhost:11434/v1")

    with pytest.raises(OllamaConnectionError) as excinfo:
        client.triage_event(EVENT, make_rule_result(False))

    message = str(excinfo.value)
    assert "http://localhost:11434/v1" in message
    assert "ollama pull test-model" in message
    assert mock_post.call_count == 1  # connection refused: no retry


@patch("triage.llm_client.time.sleep")
@patch("triage.llm_client.requests.post")
def test_timeout_retries_once_then_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [
        requests.exceptions.Timeout("timed out"),
        make_response(tool_call_message("Recovered.", False)),
    ]
    result = LLMClient(model="test-model").triage_event(EVENT, make_rule_result(False))

    assert result["summary"] == "Recovered."
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()


@patch("triage.llm_client.time.sleep")
@patch("triage.llm_client.requests.post")
def test_timeout_twice_raises(mock_post, mock_sleep):
    mock_post.side_effect = requests.exceptions.Timeout("timed out")
    with pytest.raises(requests.exceptions.Timeout):
        LLMClient(model="test-model").triage_event(EVENT, make_rule_result(False))
    assert mock_post.call_count == 2


@patch("triage.llm_client.time.sleep")
@patch("triage.llm_client.requests.post")
def test_connection_error_on_retry_maps_to_ollama_error(mock_post, mock_sleep):
    mock_post.side_effect = [
        requests.exceptions.Timeout("timed out"),
        requests.exceptions.ConnectionError("refused"),
    ]
    with pytest.raises(OllamaConnectionError):
        LLMClient(model="test-model").triage_event(EVENT, make_rule_result(False))


def test_stub_client_mirrors_rule_result():
    for reportable in (True, False):
        result = StubClient().triage_event(EVENT, make_rule_result(reportable))
        assert result["summary"].startswith("[stub] ")
        assert result["llm_reportable"] is reportable
        assert result["llm_reasoning"]

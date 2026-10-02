from __future__ import annotations

import csv
import logging
from typing import Any, Dict, Iterator, List, Optional

from . import schema, rules
from .llm_client import OllamaConnectionError

logger = logging.getLogger(__name__)


def read_rows(csv_path: str, limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            yield row


def run_pipeline(
    csv_path: str,
    config_path: str,
    llm_client,
    limit: Optional[int] = None,
    on_progress=None,
    on_skip=None,
) -> List[Dict[str, Any]]:
    config = schema.load_config(config_path)
    results = []

    for i, raw_row in enumerate(read_rows(csv_path, limit=limit)):
        try:
            event = schema.normalize_row(raw_row, config)
            rule_result = rules.classify_event(event, config)
            llm_result = llm_client.triage_event(event, rule_result)
        except OllamaConnectionError:
            # Ollama is down entirely — every remaining row would fail the
            # same way, so abort with the actionable message instead of
            # logging a warning per row.
            raise
        except Exception as exc:
            logger.warning("Skipping row %d: %s", i + 1, exc)
            if on_skip:
                on_skip(i + 1, exc)
            continue

        results.append(
            {
                "event_id": event["event_id"],
                "narrative": event["narrative"],
                "fields": event["fields"],
                "rule_severity": rule_result["rule_severity"],
                "rule_reportable": rule_result["rule_reportable"],
                "rule_reasons": rule_result["matched_reasons"],
                "summary": llm_result["summary"],
                "reportable": llm_result["llm_reportable"],
                "llm_reasoning": llm_result["llm_reasoning"],
            }
        )
        if on_progress:
            on_progress(i + 1, event["event_id"])

    return results

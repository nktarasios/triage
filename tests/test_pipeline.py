"""End-to-end pipeline test over the fixture CSV using StubClient — no network."""
import logging
from pathlib import Path

import pytest

from triage.llm_client import OllamaConnectionError, StubClient
from triage.pipeline import run_pipeline

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = str(REPO_ROOT / "config" / "nhtsa_sgo_ads.yaml")
FIXTURE_CSV = str(REPO_ROOT / "tests" / "fixtures" / "sample_events.csv")

RESULT_KEYS = {
    "event_id",
    "narrative",
    "fields",
    "rule_severity",
    "rule_reportable",
    "rule_reasons",
    "summary",
    "reportable",
    "llm_reasoning",
}


def test_pipeline_end_to_end_with_stub():
    results = run_pipeline(FIXTURE_CSV, CONFIG_PATH, StubClient())

    assert len(results) == 14
    severities = {r["rule_severity"] for r in results}
    assert {"critical", "high", "medium", "low"} <= severities
    for result in results:
        assert set(result) == RESULT_KEYS
        assert result["event_id"]


def test_pipeline_respects_limit():
    results = run_pipeline(FIXTURE_CSV, CONFIG_PATH, StubClient(), limit=3)
    assert len(results) == 3


def test_pipeline_reports_progress():
    seen = []
    run_pipeline(
        FIXTURE_CSV,
        CONFIG_PATH,
        StubClient(),
        limit=2,
        on_progress=lambda n, event_id: seen.append((n, event_id)),
    )
    assert [n for n, _ in seen] == [1, 2]


class FailOnEventClient(StubClient):
    """Raises on one specific event to simulate a per-row processing failure."""

    def __init__(self, fail_event_id):
        self.fail_event_id = fail_event_id

    def triage_event(self, event, rule_result):
        if event["event_id"] == self.fail_event_id:
            raise RuntimeError(f"boom on {event['event_id']}")
        return super().triage_event(event, rule_result)


def test_pipeline_skips_failing_row_and_continues(caplog):
    # 30270-15069 is the 2nd row of the fixture CSV.
    skipped = []
    with caplog.at_level(logging.WARNING, logger="triage.pipeline"):
        results = run_pipeline(
            FIXTURE_CSV,
            CONFIG_PATH,
            FailOnEventClient("30270-15069"),
            on_skip=lambda row_number, exc: skipped.append((row_number, exc)),
        )

    assert len(results) == 13
    assert "30270-15069" not in {r["event_id"] for r in results}
    assert [n for n, _ in skipped] == [2]
    assert "Skipping row 2" in caplog.text


class DownClient:
    """Simulates Ollama being unreachable for every event."""

    def triage_event(self, event, rule_result):
        raise OllamaConnectionError("Could not reach Ollama at http://test")


def test_pipeline_aborts_when_ollama_is_down():
    skipped = []
    with pytest.raises(OllamaConnectionError):
        run_pipeline(
            FIXTURE_CSV,
            CONFIG_PATH,
            DownClient(),
            on_skip=lambda row_number, exc: skipped.append(row_number),
        )
    assert skipped == []  # aborted, not skipped-and-continued

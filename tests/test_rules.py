"""Regression baseline for the rules engine.

Every fixture row's classification must match
tests/fixtures/expected_classifications.json exactly. If a test here fails
and you believe the BASELINE (not the code) is wrong, stop and flag it —
do not regenerate the baseline silently (see docs/SPEC.md).
"""
import csv
import json
from pathlib import Path

import pytest

from triage import rules, schema

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "nhtsa_sgo_ads.yaml"
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "sample_events.csv"
BASELINE_JSON = REPO_ROOT / "tests" / "fixtures" / "expected_classifications.json"

CONFIG = schema.load_config(str(CONFIG_PATH))

with open(BASELINE_JSON, "r", encoding="utf-8") as f:
    BASELINE = {entry["event_id"]: entry for entry in json.load(f)}

with open(FIXTURE_CSV, "r", encoding="utf-8", errors="replace") as f:
    ROWS = list(csv.DictReader(f))


def test_baseline_covers_every_fixture_row():
    fixture_ids = {row["Report ID"] for row in ROWS}
    assert fixture_ids == set(BASELINE)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["Report ID"])
def test_classification_matches_baseline(row):
    event = schema.normalize_row(row, CONFIG)
    result = rules.classify_event(event, CONFIG)
    expected = BASELINE[event["event_id"]]

    assert result["rule_severity"] == expected["rule_severity"]
    assert result["rule_reportable"] == expected["rule_reportable"]
    assert set(result["matched_reasons"]) == set(expected["matched_reasons"])

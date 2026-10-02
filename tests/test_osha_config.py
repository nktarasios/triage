"""Proves the agent is dataset-agnostic: the OSHA Severe Injury Reports
config drives the same unchanged code against a completely different domain
(workplace injuries vs. AV crashes).

Fixture rows are real reports sampled from the public OSHA SIR dataset
(https://www.osha.gov/severe-injury-reports). Every real SIR row carries at
least one statutory trigger (amputation, hospitalization, loss of an eye) —
the dataset is by definition already-reported events — so the keyword-only
and no-match paths are exercised with constructed rows instead.
"""
import csv
from pathlib import Path

import pytest

from triage import rules, schema

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "osha_severe_injury.yaml"
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "osha_sample_events.csv"

CONFIG = schema.load_config(str(CONFIG_PATH))

with open(FIXTURE_CSV, "r", encoding="utf-8", errors="replace") as f:
    ROWS = {row["ID"]: row for row in csv.DictReader(f)}

EXPECTED = {
    # hospitalization + fracture keyword
    "2015010015": ("high", True, {"In-patient hospitalization reported", "Fracture reported"}),
    # hospitalization only
    "2015010018": ("high", True, {"In-patient hospitalization reported"}),
    # amputation, no hospitalization
    "2015010022": ("critical", True, {"Amputation reported"}),
    # amputation + hospitalization
    "2015010041": ("critical", True, {"Amputation reported", "In-patient hospitalization reported"}),
    # loss of an eye
    "2015063437": ("critical", True, {"Loss of an eye reported"}),
}


@pytest.mark.parametrize("event_id", sorted(EXPECTED), ids=str)
def test_real_osha_rows_classify_correctly(event_id):
    severity, reportable, reasons = EXPECTED[event_id]
    event = schema.normalize_row(ROWS[event_id], CONFIG)
    result = rules.classify_event(event, CONFIG)

    assert result["rule_severity"] == severity
    assert result["rule_reportable"] == reportable
    assert set(result["matched_reasons"]) == reasons


def make_row(**overrides):
    row = {
        "ID": "test-row",
        "Final Narrative": "Constructed row for rule-path coverage.",
        "Hospitalized": "0.00",
        "Amputation": "0.00",
        "Loss of Eye": "0.00",
        "NatureTitle": "Bruises, contusions",
    }
    row.update(overrides)
    return row


def test_fracture_without_trigger_is_medium_not_reportable():
    event = schema.normalize_row(make_row(NatureTitle="Fractures"), CONFIG)
    result = rules.classify_event(event, CONFIG)
    assert result["rule_severity"] == "medium"
    assert result["rule_reportable"] is False
    assert result["matched_reasons"] == ["Fracture reported"]


def test_burn_without_trigger_is_medium_not_reportable():
    event = schema.normalize_row(
        make_row(NatureTitle="Heat (thermal) burns, unspecified"), CONFIG
    )
    result = rules.classify_event(event, CONFIG)
    assert result["rule_severity"] == "medium"
    assert result["rule_reportable"] is False
    assert result["matched_reasons"] == ["Burn injury reported"]


def test_no_matching_rule_defaults_to_low():
    event = schema.normalize_row(make_row(), CONFIG)
    result = rules.classify_event(event, CONFIG)
    assert result["rule_severity"] == "low"
    assert result["rule_reportable"] is False
    assert result["matched_reasons"] == []

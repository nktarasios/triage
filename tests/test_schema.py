"""Tests for triage.schema: config loading and row normalization."""
import csv
from pathlib import Path

import pytest

from triage import schema

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "nhtsa_sgo_ads.yaml"
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "sample_events.csv"


@pytest.fixture(scope="module")
def config():
    return schema.load_config(str(CONFIG_PATH))


@pytest.fixture(scope="module")
def rows_by_id():
    with open(FIXTURE_CSV, "r", encoding="utf-8", errors="replace") as f:
        return {row["Report ID"]: row for row in csv.DictReader(f)}


def test_load_config_has_required_keys(config):
    for key in ["dataset_name", "id_field", "narrative_field", "fields", "rules"]:
        assert key in config


def test_load_config_missing_keys_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("dataset_name: incomplete\nid_field: ID\n")
    with pytest.raises(ValueError, match="missing required keys"):
        schema.load_config(str(bad))


BASE_CONFIG = """\
dataset_name: test
id_field: ID
narrative_field: Narrative
fields:
  injury_severity: Injury
rules:
{rules}
"""


def write_config(tmp_path, rules_yaml):
    path = tmp_path / "config.yaml"
    path.write_text(BASE_CONFIG.format(rules=rules_yaml))
    return str(path)


def test_keyword_rule_missing_severity_raises(tmp_path):
    path = write_config(
        tmp_path,
        "  keyword_rules:\n"
        "    - field: injury_severity\n"
        '      contains: ["Fatality"]\n'
        "      reason: Fatality alleged\n",
    )
    with pytest.raises(ValueError, match=r"keyword_rules\[0\].*'severity'") as excinfo:
        schema.load_config(path)
    assert "Fatality alleged" in str(excinfo.value)


def test_keyword_rule_without_contains_or_not_contains_raises(tmp_path):
    path = write_config(
        tmp_path,
        "  keyword_rules:\n"
        "    - field: injury_severity\n"
        "      severity: high\n"
        "      reason: Bad rule\n",
    )
    with pytest.raises(
        ValueError, match=r"keyword_rules\[0\].*'contains' or 'not_contains'"
    ):
        schema.load_config(path)


def test_numeric_rule_missing_threshold_raises(tmp_path):
    path = write_config(
        tmp_path,
        "  numeric_rules:\n"
        "    - field: injury_severity\n"
        '      operator: ">"\n'
        "      severity: medium\n"
        "      reason: Speedy\n",
    )
    with pytest.raises(ValueError, match=r"numeric_rules\[0\].*'threshold'"):
        schema.load_config(path)


def test_numeric_rule_invalid_operator_raises(tmp_path):
    path = write_config(
        tmp_path,
        "  numeric_rules:\n"
        "    - field: injury_severity\n"
        '      operator: "!="\n'
        "      threshold: 35\n"
        "      severity: medium\n"
        "      reason: Speedy\n",
    )
    with pytest.raises(ValueError, match=r"numeric_rules\[0\].*invalid operator"):
        schema.load_config(path)


def test_keyword_rule_invalid_severity_raises(tmp_path):
    path = write_config(
        tmp_path,
        "  keyword_rules:\n"
        "    - field: injury_severity\n"
        '      contains: ["Fatality"]\n'
        "      severity: catastrophic\n"
        "      reason: Fatality alleged\n",
    )
    with pytest.raises(ValueError, match=r"keyword_rules\[0\].*invalid severity"):
        schema.load_config(path)


def test_normalize_fatality_row(config, rows_by_id):
    event = schema.normalize_row(rows_by_id["30270-11713"], config)
    assert event["event_id"] == "30270-11713"
    assert event["narrative"]
    assert event["fields"]["injury_severity"] == "Fatality"
    assert event["fields"]["crash_with"] == "Motorcycle"
    assert event["fields"]["vehicle_towed"] == (
        "Yes Subject Vehicle, Unknown Crash Partner"
    )
    assert event["fields"]["precrash_speed_mph"] == "8"
    assert event["fields"]["city"] == "Tempe"
    assert event["fields"]["state"] == "AZ"


def test_normalize_minor_injury_row(config, rows_by_id):
    event = schema.normalize_row(rows_by_id["30270-15177"], config)
    assert event["event_id"] == "30270-15177"
    assert event["narrative"]
    assert event["fields"]["injury_severity"] == "Minor W/O Hospitalization"
    assert event["fields"]["crash_with"] == "SUV"
    assert event["fields"]["vehicle_towed"] == "No Subject Vehicle, No Crash Partner"
    assert event["fields"]["precrash_speed_mph"] == "2"
    assert event["fields"]["city"] == "Los Angeles"


def test_normalize_property_damage_row(config, rows_by_id):
    event = schema.normalize_row(rows_by_id["30270-15029"], config)
    assert event["event_id"] == "30270-15029"
    assert event["narrative"]
    assert event["fields"]["injury_severity"] == (
        "Property Damage. No Injured Reported"
    )
    assert event["fields"]["airbag_deployed"] == (
        "No Subject Vehicle, No Crash Partner"
    )
    assert event["fields"]["city"] == "San Francisco"


def test_normalize_row_with_missing_fields(config):
    event = schema.normalize_row({"Report ID": "test-123"}, config)
    assert event["event_id"] == "test-123"
    assert event["narrative"] == ""
    for canonical_name in config["fields"]:
        assert event["fields"][canonical_name] == ""

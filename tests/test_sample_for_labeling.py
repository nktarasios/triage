"""Tests for eval/sample_for_labeling.py. No Ollama, no network."""
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
ADS_CSV = REPO_ROOT / "data" / "sgo_ads.csv"
HOLDOUT_PATH = REPO_ROOT / "tests" / "eval" / "holdout_60.json"
LABELS_PATH = REPO_ROOT / "tests" / "eval" / "labeled_events.json"
TO_REVIEW_PATH = REPO_ROOT / "tests" / "eval" / "to_review.json"

_spec = importlib.util.spec_from_file_location(
    "sample_for_labeling", REPO_ROOT / "eval" / "sample_for_labeling.py"
)
sample = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sample)


MINI_CONFIG = """\
dataset_name: test
id_field: ID
narrative_field: Narrative
fields:
  injury_severity: Injury
rules:
  keyword_rules:
    - field: injury_severity
      contains: ["Fatality"]
      severity: critical
      reportable: true
      reason: "Fatality alleged"
  default_severity: low
  default_reportable: false
"""


def test_negated_injury_boilerplate_is_not_a_cue():
    for text in (
        "Both vehicles sustained damage. No injuries were reported.",
        "There were no reported injuries.",
        "The passengers did not report any injuries, and police were not notified.",
        "No one was injured.",
    ):
        assert "injury" not in sample.narrative_cues(text), text


def test_alleged_injury_is_a_cue_even_after_a_negated_sentence():
    assert "injury" in sample.narrative_cues(
        "A passenger in the vehicle alleged a minor injury."
    )
    assert "injury" in sample.narrative_cues(
        "No injuries were reported at that time. "
        "The passenger later alleged a soft tissue injury."
    )
    assert "injury" in sample.narrative_cues("The dog sustained injuries.")


def test_animal_vru_rail_fire_and_rollover_cues_skip_lookalikes():
    assert "animal" in sample.narrative_cues(
        "The vehicle made contact with a duck lying in the road."
    )
    assert "vru" in sample.narrative_cues(
        "A cyclist on an electric bicycle made contact with the rear."
    )
    assert "rail" in sample.narrative_cues(
        "The vehicle drove under a lowering railroad crossing gate."
    )
    assert "fire" in sample.narrative_cues("The vehicle caught fire after the impact.")
    assert "rollover" in sample.narrative_cues("The SUV rolled over onto its roof.")

    assert sample.narrative_cues(
        "The trailer made contact while the vehicle yielded to a fire truck "
        "and then rolled forward."
    ) == []


def test_rules_reportable_event_is_not_narrative_only():
    assert sample.stratum_for(False, ["animal"]) == "narrative_only"
    assert sample.stratum_for(False, []) == "remainder"
    assert sample.stratum_for(True, ["injury", "vru"]) == "remainder"


def test_stratified_sample_takes_every_narrative_only_id_when_the_pool_is_small():
    narrative = [f"n{i}" for i in range(3)]
    remainder = [f"r{i}" for i in range(20)]
    chosen = sample.stratified_ids(narrative, remainder, n=10, seed=sample.DEFAULT_SEED)
    assert len(chosen) == 10
    assert set(narrative) <= set(chosen)
    assert len(set(chosen)) == 10


def test_stratified_sample_does_not_undersample_a_common_stratum():
    narrative = [f"n{i:02d}" for i in range(80)]
    remainder = [f"r{i:02d}" for i in range(20)]
    chosen = sample.stratified_ids(
        narrative, remainder, n=10, seed=1, narrative_fraction=0.5
    )
    # Base rate is 80%. A 50% target must not pull the stratum below that.
    assert sum(event_id.startswith("n") for event_id in chosen) == 8
    assert len(chosen) == 10


def test_stratified_sample_is_reproducible_and_ignores_input_order():
    narrative = ["n2", "n1", "n0"]
    remainder = ["r5", "r4", "r3", "r2", "r0", "r1"]
    first = sample.stratified_ids(narrative, remainder, n=4, seed=7)
    second = sample.stratified_ids(
        list(reversed(narrative)), list(reversed(remainder)), n=4, seed=7
    )
    assert first == second
    assert len(first) == 4
    # 3 of 9 is below the 50% floor, so the quota is 2 and the rest is remainder.
    assert sum(event_id.startswith("n") for event_id in first) == 2

    other_seed = sample.stratified_ids(narrative, remainder, n=4, seed=8)
    assert other_seed != first


def test_draft_rationale_is_one_rules_only_line():
    rationale = sample.draft_rationale(
        {
            "rule_reportable": False,
            "rule_severity": "low",
            "matched_reasons": [],
        },
        ["injury", "animal"],
    )
    assert "\n" not in rationale
    assert rationale.startswith("DRAFT from the rules engine, not a human label.")
    assert "Not reportable (low)" in rationale
    assert "injury, animal" in rationale
    spelled_out = sample.draft_rationale(
        {"rule_reportable": False, "rule_severity": "low", "matched_reasons": []},
        ["vru"],
    )
    assert "vulnerable road user" in spelled_out
    assert " vru" not in spelled_out

    record = sample.draft_record(
        {"event_id": "e1", "narrative": "hit a dog", "fields": {"city": "Austin"}},
        {
            "rule_reportable": False,
            "rule_severity": "low",
            "matched_reasons": [],
        },
        ["animal"],
        "narrative_only",
        seed=5,
    )
    assert record["human_reportable"] is False
    assert record["reportable"] is False
    assert record["rule_reportable"] is False
    assert record["summary"] == record["notes"]
    assert record["notes"].startswith("DRAFT from the rules engine, not a human label.")
    assert "animal" in record["notes"]
    assert record["label_status"] == "draft"
    assert record["llm_reasoning"] == "Rules-only pre-fill. No model was called."


def test_draft_reportable_follows_the_rules_floor():
    record = sample.draft_record(
        {"event_id": "e2", "narrative": "fatality", "fields": {}},
        {
            "rule_reportable": True,
            "rule_severity": "critical",
            "matched_reasons": ["Fatality alleged"],
        },
        [],
        "remainder",
        seed=5,
    )
    assert record["human_reportable"] is True
    assert record["reportable"] is True
    assert "Reportable (critical): Fatality alleged." in record["notes"]


def _write_mini_dataset(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(MINI_CONFIG)
    csv_path = tmp_path / "events.csv"
    rows = [
        ("hold-1", "Ordinary contact in a queue.", "Property Damage"),
        ("dog-1", "The vehicle made contact with a duck.", "Property Damage"),
        ("dog-1", "OLDER VERSION should be ignored.", "Property Damage"),
        ("inj-1", "A passenger alleged a minor injury.", "Property Damage"),
        ("neg-1", "No injuries were reported.", "Property Damage"),
        ("fat-1", "Narrative mentions a pedestrian.", "Fatality"),
        ("clear-1", "Low speed contact with a parked car.", "Property Damage"),
        ("clear-2", "The trailer brushed the mirror.", "Property Damage"),
        ("clear-3", "Yielded, then was struck from behind.", "Property Damage"),
        ("clear-4", "A fire truck was passing. No contact with it.", "Property Damage"),
    ]
    lines = ["ID,Narrative,Injury"]
    for event_id, narrative, injury in rows:
        lines.append(f'{event_id},"{narrative}",{injury}')
    csv_path.write_text("\n".join(lines) + "\n")
    labels_path = tmp_path / "labeled.json"
    labels_path.write_text(
        json.dumps(
            [
                {
                    "event_id": "hold-1",
                    "human_reportable": False,
                    "notes": "already reviewed",
                }
            ],
            indent=2,
        )
        + "\n"
    )
    return config_path, csv_path, labels_path


def test_cli_freezes_holdout_and_writes_rules_only_drafts(tmp_path):
    config_path, csv_path, labels_path = _write_mini_dataset(tmp_path)
    holdout_path = tmp_path / "holdout.json"
    output_path = tmp_path / "to_review.json"

    code = sample.main(
        [
            "--input", str(csv_path),
            "--config", str(config_path),
            "--labels", str(labels_path),
            "--holdout", str(holdout_path),
            "--output", str(output_path),
            "--n", "4",
            "--seed", "20261002",
        ]
    )
    assert code == 0
    assert holdout_path.read_bytes() == labels_path.read_bytes()

    drafts = json.loads(output_path.read_text())
    ids = [record["event_id"] for record in drafts]
    assert len(ids) == len(set(ids)) == 4
    assert "hold-1" not in ids
    assert {"dog-1", "inj-1"} <= set(ids)
    by_id = {record["event_id"]: record for record in drafts}
    assert by_id["dog-1"]["narrative"] == "The vehicle made contact with a duck."
    assert by_id["dog-1"]["sample_stratum"] == "narrative_only"
    assert by_id["dog-1"]["human_reportable"] is False
    assert by_id["dog-1"]["narrative_cues"] == ["animal"]
    assert "injury" not in by_id.get("neg-1", {"narrative_cues": []})["narrative_cues"]
    if "fat-1" in by_id:
        assert by_id["fat-1"]["human_reportable"] is True
        assert by_id["fat-1"]["sample_stratum"] == "remainder"

    # Growing the working label file must not change the frozen exclusion
    # list or the draw.
    labels = json.loads(labels_path.read_text())
    labels.append(
        {"event_id": "clear-1", "human_reportable": False, "notes": "added later"}
    )
    labels_path.write_text(json.dumps(labels))
    frozen = holdout_path.read_bytes()
    second_output = tmp_path / "to_review_again.json"
    code = sample.main(
        [
            "--input", str(csv_path),
            "--config", str(config_path),
            "--labels", str(labels_path),
            "--holdout", str(holdout_path),
            "--output", str(second_output),
            "--n", "4",
            "--seed", "20261002",
        ]
    )
    assert code == 0
    assert holdout_path.read_bytes() == frozen
    assert json.loads(second_output.read_text()) == drafts


def test_build_review_records_refuses_a_short_pool(tmp_path):
    config_path, csv_path, labels_path = _write_mini_dataset(tmp_path)
    config = sample.schema.load_config(str(config_path))
    events = sample.load_candidate_events(
        str(csv_path), config, {"hold-1"}
    )
    with pytest.raises(ValueError, match="only"):
        sample.build_review_records(events, config, n=50, seed=1)


def test_sampler_does_not_name_source_columns_or_call_a_model():
    source = (REPO_ROOT / "eval" / "sample_for_labeling.py").read_text()
    assert "llm_client" not in source
    assert "openai" not in source.lower()
    for column in (
        "Report ID",
        "Crash With",
        "Highest Injury Severity Alleged",
        "Was Any Vehicle Towed?",
    ):
        assert column not in source


def test_shipped_draw_is_a_draft_batch_outside_the_holdout():
    """The committed files are the Phase A artifacts. Drafts are not labels."""
    assert HOLDOUT_PATH.is_file()
    assert TO_REVIEW_PATH.is_file()
    assert HOLDOUT_PATH.read_bytes() == LABELS_PATH.read_bytes()

    holdout = json.loads(HOLDOUT_PATH.read_text())
    drafts = json.loads(TO_REVIEW_PATH.read_text())
    assert len(holdout) == 60
    assert len(drafts) == 140

    holdout_ids = [entry["event_id"] for entry in holdout]
    draft_ids = [entry["event_id"] for entry in drafts]
    assert len(holdout_ids) == len(set(holdout_ids))
    assert len(draft_ids) == len(set(draft_ids))
    assert set(draft_ids).isdisjoint(holdout_ids)
    assert draft_ids == sorted(draft_ids)

    narrative_only = 0
    for record in drafts:
        assert record["label_status"] == "draft"
        assert record["sample_seed"] == sample.DEFAULT_SEED
        assert record["human_reportable"] is record["rule_reportable"] is record["reportable"]
        assert isinstance(record["human_reportable"], bool)
        assert record["summary"] == record["notes"]
        assert "\n" not in record["notes"]
        assert record["notes"].startswith("DRAFT from the rules engine")
        assert record["llm_reasoning"] == "Rules-only pre-fill. No model was called."
        assert set(record["narrative_cues"]) <= set(sample.NARRATIVE_CUES)
        assert record["narrative_cues"] == sample.narrative_cues(record["narrative"])
        expected = sample.stratum_for(record["rule_reportable"], record["narrative_cues"])
        assert record["sample_stratum"] == expected
        if expected == "narrative_only":
            narrative_only += 1
    # Base rate on the public file is a few percent. The draw keeps every
    # narrative-only event it can, which is well above a quarter of 140.
    assert narrative_only / len(drafts) >= 0.25


@pytest.mark.skipif(not ADS_CSV.is_file(), reason="public ADS CSV is fetched locally")
def test_committed_draw_reproduces_from_the_seed(tmp_path):
    output_path = tmp_path / "to_review.json"
    before = HOLDOUT_PATH.read_bytes()
    code = sample.main(
        [
            "--input", str(ADS_CSV),
            "--output", str(output_path),
            "--n", "140",
            "--seed", str(sample.DEFAULT_SEED),
        ]
    )
    assert code == 0
    assert HOLDOUT_PATH.read_bytes() == before
    assert json.loads(output_path.read_text()) == json.loads(TO_REVIEW_PATH.read_text())

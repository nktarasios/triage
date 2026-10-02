"""Unit tests for eval/run_eval.py's pure metrics logic — no Ollama, no network."""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "run_eval", REPO_ROOT / "eval" / "run_eval.py"
)
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def label(event_id, human_reportable, notes=""):
    return {
        "event_id": event_id,
        "human_reportable": human_reportable,
        "notes": notes,
    }


def result(reportable, rule_reportable=None):
    if rule_reportable is None:
        rule_reportable = reportable
    return {"reportable": reportable, "rule_reportable": rule_reportable}


def test_perfect_agreement():
    labels = [label("a", True), label("b", False)]
    agent = {"a": result(True), "b": result(False)}
    report = run_eval.compute_report(labels, agent)

    assert report["evaluated"] == 2
    assert report["agreement_rate"] == 1.0
    assert report["false_negatives"] == []
    assert report["false_positives"] == []
    assert report["missing"] == []
    assert report["escalations"] == {
        "total": 0, "human_agreed": 0, "precision": None,
    }


def test_false_negative_and_false_positive_land_on_right_sides():
    labels = [
        label("fn", True),   # human: reportable, agent: not
        label("fp", False),  # human: not, agent: reportable
        label("ok", True),
    ]
    agent = {"fn": result(False), "fp": result(True), "ok": result(True)}
    report = run_eval.compute_report(labels, agent)

    assert report["evaluated"] == 3
    assert report["agreement_rate"] == 1 / 3
    assert report["false_negatives"] == ["fn"]
    assert report["false_positives"] == ["fp"]


def test_labeled_event_missing_from_results():
    labels = [label("present", True), label("absent", True)]
    agent = {"present": result(True)}
    report = run_eval.compute_report(labels, agent)

    assert report["evaluated"] == 1
    assert report["agreement_rate"] == 1.0
    assert report["missing"] == ["absent"]


def test_empty_labels():
    report = run_eval.compute_report([], {})
    assert report["evaluated"] == 0
    assert report["agreement_rate"] is None
    assert report["false_negatives"] == []
    assert report["false_positives"] == []
    assert report["missing"] == []


def test_false_positives_attributed_to_source():
    labels = [
        label("esc-bad", False),   # LLM escalated, human disagreed
        label("rule-bad", False),  # rules flagged it, human disagreed
        label("esc-good", True),   # LLM escalated, human agreed
    ]
    agent = {
        "esc-bad": result(True, rule_reportable=False),
        "rule-bad": result(True, rule_reportable=True),
        "esc-good": result(True, rule_reportable=False),
    }
    report = run_eval.compute_report(labels, agent)

    assert report["fp_from_escalation"] == ["esc-bad"]
    assert report["fp_from_rules"] == ["rule-bad"]
    assert report["escalations"] == {
        "total": 2, "human_agreed": 1, "precision": 0.5,
    }


def test_missing_rule_reportable_is_tolerated():
    labels = [label("a", False)]
    agent = {"a": {"reportable": True}}  # no rule_reportable key
    report = run_eval.compute_report(labels, agent)

    assert report["false_positives"] == ["a"]
    assert report["fp_from_escalation"] == []
    assert report["fp_from_rules"] == []
    assert report["escalations"]["total"] == 0


def test_load_results_file_filters_to_labeled_ids(tmp_path):
    import json

    path = tmp_path / "triaged.json"
    path.write_text(json.dumps([
        {"event_id": "a", "reportable": True, "rule_reportable": False},
        {"event_id": "b", "reportable": False, "rule_reportable": False},
    ]))
    loaded = run_eval.load_results_file(str(path), {"a"})
    assert set(loaded) == {"a"}
    assert loaded["a"]["reportable"] is True


def test_shipped_label_file_is_valid_ground_truth():
    # The shipped file carries the real hand-reviewed labels the README's
    # measured results are based on; every entry must be usable by run_eval.
    import json

    with open(REPO_ROOT / "tests" / "eval" / "labeled_events.json") as f:
        labels = json.load(f)

    assert isinstance(labels, list) and len(labels) > 0
    ids = [entry["event_id"] for entry in labels]
    assert len(ids) == len(set(ids))
    for entry in labels:
        assert isinstance(entry["event_id"], str) and entry["event_id"]
        assert isinstance(entry["human_reportable"], bool)
        assert isinstance(entry["notes"], str)

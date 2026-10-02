#!/usr/bin/env python3
"""
Evaluation harness: measures the agent's reportability decisions against
hand-written human labels.

Labels live in tests/eval/labeled_events.json as a list of:
    {"event_id": str, "human_reportable": bool, "notes": str}

This runs the FULL pipeline for each labeled event — rules engine plus a
real Ollama LLM call (not the stub) — so it measures what the agent would
actually do in production. Only labeled events are sent to the LLM.

Example:
    python eval/run_eval.py --input data/sgo_ads.csv
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from triage import rules, schema  # noqa: E402
from triage.llm_client import LLMClient, OllamaConnectionError  # noqa: E402
from triage.pipeline import read_rows  # noqa: E402

logger = logging.getLogger("triage.eval")


def compute_report(labels, agent_results):
    """Compare human labels against agent results.

    labels: list of {"event_id", "human_reportable", "notes"}
    agent_results: dict of event_id -> {"reportable": bool,
        "rule_reportable": bool, ...}

    Disagreements are attributed to their source: a false positive where
    the rules said "not reportable" but the final answer is "reportable"
    came from the LLM escalation; one where the rules themselves flagged
    it came from the rules. Escalation precision (of the LLM's
    escalations, how many the human agreed with) is the key number for
    tuning the escalation prompt.

    Pure function — no I/O — so it's unit-testable without Ollama.
    """
    false_negatives = []
    false_positives = []
    fp_from_escalation = []
    fp_from_rules = []
    missing = []
    agreements = 0
    escalations = []
    escalations_agreed = []

    for label in labels:
        event_id = label["event_id"]
        if event_id not in agent_results:
            missing.append(event_id)
            continue
        result = agent_results[event_id]
        agent_reportable = result["reportable"]
        rule_reportable = result.get("rule_reportable")
        human_reportable = label["human_reportable"]

        escalated = agent_reportable and rule_reportable is False
        if escalated:
            escalations.append(event_id)
            if human_reportable:
                escalations_agreed.append(event_id)

        if agent_reportable == human_reportable:
            agreements += 1
        elif human_reportable and not agent_reportable:
            false_negatives.append(event_id)
        else:
            false_positives.append(event_id)
            if escalated:
                fp_from_escalation.append(event_id)
            elif rule_reportable:
                fp_from_rules.append(event_id)

    evaluated = len(labels) - len(missing)
    return {
        "evaluated": evaluated,
        "agreement_rate": agreements / evaluated if evaluated else None,
        "false_negatives": false_negatives,
        "false_positives": false_positives,
        "fp_from_escalation": fp_from_escalation,
        "fp_from_rules": fp_from_rules,
        "escalations": {
            "total": len(escalations),
            "human_agreed": len(escalations_agreed),
            "precision": (
                len(escalations_agreed) / len(escalations) if escalations else None
            ),
        },
        "missing": missing,
    }


def run_agent_on_labeled_events(args, labeled_ids):
    """Run rules + real LLM for just the labeled events. Returns
    {event_id: result dict} in the same shape as pipeline results."""
    config = schema.load_config(args.config)
    llm_client = LLMClient(args.model, args.base_url)
    results = {}

    for raw_row in read_rows(args.input):
        event = schema.normalize_row(raw_row, config)
        if event["event_id"] not in labeled_ids:
            continue
        rule_result = rules.classify_event(event, config)
        llm_result = llm_client.triage_event(event, rule_result)
        results[event["event_id"]] = {
            "rule_severity": rule_result["rule_severity"],
            "rule_reportable": rule_result["rule_reportable"],
            "reportable": llm_result["llm_reportable"],
            "summary": llm_result["summary"],
        }
        logger.info(
            "  evaluated %s (rules: %s, final: %s)",
            event["event_id"],
            rule_result["rule_reportable"],
            llm_result["llm_reportable"],
        )
        if len(results) == len(labeled_ids):
            break

    return results


def load_results_file(path, labeled_ids):
    """Offline mode: evaluate an existing pipeline output JSON (e.g.
    output/triaged.json) instead of re-running the LLM. This measures the
    exact run the reviewer labeled, and costs nothing."""
    with open(path, "r", encoding="utf-8") as f:
        results = json.load(f)
    return {r["event_id"]: r for r in results if r["event_id"] in labeled_ids}


def print_report(report, labels_by_id):
    print(f"\nEvaluated {report['evaluated']} labeled event(s).")
    if report["agreement_rate"] is not None:
        print(f"Agreement rate: {report['agreement_rate']:.1%}")

    fn = report["false_negatives"]
    print(
        f"\nFALSE NEGATIVES (human: reportable, agent: not) — "
        f"REVIEW THESE: {len(fn)}"
    )
    for event_id in fn:
        notes = labels_by_id[event_id].get("notes", "")
        print(f"  !! {event_id}  {notes}")

    fp = report["false_positives"]
    print(f"\nFalse positives (human: not reportable, agent: reportable): {len(fp)}")
    for event_id in fp:
        src = (
            "LLM escalation" if event_id in report["fp_from_escalation"]
            else "rules" if event_id in report["fp_from_rules"]
            else "unknown source"
        )
        notes = labels_by_id[event_id].get("notes", "")
        print(f"     {event_id}  [{src}]  {notes}")

    esc = report["escalations"]
    print(f"\nLLM escalations among evaluated events: {esc['total']}")
    if esc["precision"] is not None:
        print(
            f"Escalation precision (human agreed with the escalation): "
            f"{esc['human_agreed']}/{esc['total']} = {esc['precision']:.1%}"
        )

    if report["missing"]:
        print(f"\nLabeled events not found in the results: {report['missing']}")


def save_report(path, report, labels_by_id, agent_results, source):
    detail = []
    for event_id, label in sorted(labels_by_id.items()):
        result = agent_results.get(event_id)
        detail.append({
            "event_id": event_id,
            "human_reportable": label["human_reportable"],
            "agent_reportable": result["reportable"] if result else None,
            "rule_reportable": result.get("rule_reportable") if result else None,
            "notes": label.get("notes", ""),
        })
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "report": report,
        "events": detail,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    logger.info("Report saved to %s", path)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate agent reportability decisions against human labels"
    )
    parser.add_argument(
        "--input", default=str(REPO_ROOT / "data" / "sgo_ads.csv"),
        help="Path to input CSV (default: data/sgo_ads.csv)",
    )
    parser.add_argument(
        "--config", default=str(REPO_ROOT / "config" / "nhtsa_sgo_ads.yaml"),
        help="Path to dataset YAML config",
    )
    parser.add_argument(
        "--labels", default=str(REPO_ROOT / "tests" / "eval" / "labeled_events.json"),
        help="Path to hand-labeled events JSON",
    )
    parser.add_argument(
        "--model", default="qwen2.5:14b", help="Ollama model name"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434/v1",
        help="Ollama OpenAI-compatible endpoint",
    )
    parser.add_argument(
        "--results",
        default=None,
        help="Evaluate an existing pipeline output JSON (e.g. "
        "output/triaged.json) instead of re-running the LLM — measures the "
        "exact run that was labeled, no Ollama needed",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="Also write the report (plus per-event detail) to this JSON "
        "path, e.g. tests/eval/report_baseline.json",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable DEBUG-level logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    with open(args.labels, "r", encoding="utf-8") as f:
        labels = json.load(f)

    if not labels:
        print("0 labeled events, nothing to evaluate")
        print(f"(add labels to {args.labels} — "
              '{"event_id", "human_reportable", "notes"})')
        return

    labels_by_id = {label["event_id"]: label for label in labels}

    if args.results:
        logger.info(
            "Evaluating %d labeled event(s) against existing results: %s",
            len(labels), args.results,
        )
        agent_results = load_results_file(args.results, set(labels_by_id))
        source = args.results
    else:
        logger.info(
            "Running agent on %d labeled event(s): %s", len(labels), args.input
        )
        try:
            agent_results = run_agent_on_labeled_events(args, set(labels_by_id))
        except OllamaConnectionError as exc:
            logger.error("%s", exc)
            sys.exit(1)
        source = f"live run: {args.input} ({args.model})"

    report = compute_report(labels, agent_results)
    print_report(report, labels_by_id)
    if args.save:
        save_report(args.save, report, labels_by_id, agent_results, source)


if __name__ == "__main__":
    main()

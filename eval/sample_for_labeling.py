#!/usr/bin/env python3
"""
Draw the next batch of events for human labeling.

Phase A of docs/ROADMAP.md: grow the labeled set from the original 60
toward 200. This script only samples and pre-fills a rules-engine draft.
It does not label. A person reviews every draft in dashboard.html
(labeling standard: docs/DECISIONS.md #3, severity not fault) and exports
the reviewed judgments to tests/eval/labeled_events.json.

The original 60 stay a frozen holdout in tests/eval/holdout_60.json.
Once that file exists it is the exclusion list, so re-running with the
same seed reproduces the same draw even after labeled_events.json grows.

Narrative-only cases are over-represented. Those are events the rules
engine calls not reportable whose narrative mentions injury, an animal,
a vulnerable road user, rail, fire, or a rollover. Boilerplate such as
"no injuries were reported" and lookalikes such as "fire truck" or
"trailer" do not count. The cue list is sampling policy from the
roadmap, not a classification threshold, and it is applied only to the
canonical narrative. Column names and reportability thresholds stay in
the dataset config.

Rules engine only. This module never calls a model.

Example:
    python eval/sample_for_labeling.py --input data/sgo_ads.csv
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from triage import rules, schema  # noqa: E402
from triage.pipeline import read_rows  # noqa: E402

# Fixed default so `python eval/sample_for_labeling.py` is reproducible.
# Matches the roadmap date (2026-10-02).
DEFAULT_SEED = 20261002
DEFAULT_SAMPLE_SIZE = 140
# Share of the sample reserved for narrative-only events when they are
# rarer than this. The current public ADS file is about 4% narrative-only;
# 0.5 over-represents them. If they are already more common than this,
# the draw keeps at least their base rate so the boost never undersamples
# them. When fewer exist than the quota, every one of them is kept.
NARRATIVE_ONLY_FRACTION = 0.5

NARRATIVE_CUES = ("injury", "animal", "vru", "rail", "fire", "rollover")
# Human-facing names for the one-line rationale. The stored cue keys stay short.
_CUE_LABELS = {
    "injury": "injury",
    "animal": "animal",
    "vru": "vulnerable road user",
    "rail": "rail",
    "fire": "fire",
    "rollover": "rollover",
}

_INJURY_WORD = re.compile(
    r"\binjur(?:e|es|y|ies|ed|ing)\b",
    re.IGNORECASE,
)
_NEGATION_WORDS = {"no", "not", "without", "zero"}

_ANIMAL = re.compile(
    r"\b(?:animals?|dogs?|deer|geese|goose|ducks?|cats?|coyotes?|raccoons?"
    r"|birds?|horses?|squirrels?|rabbits?|livestock|wildlife|cows?|cattle"
    r"|foxes|fox|skunks?|opossums?|turkeys?)\b",
    re.IGNORECASE,
)
_VRU = re.compile(
    r"\b(?:pedestrians?|cyclists?|bicyclists?|bicycles?|motorcycles?"
    r"|motorcyclists?|scooters?|skateboards?|wheelchairs?|non-motorists?"
    r"|e-bikes?|ebikes?)\b",
    re.IGNORECASE,
)
# "rail" as its own word, so "trailer" does not match.
_RAIL = re.compile(
    r"\b(?:railroads?|railways?|rails?|trains?|grade crossings?"
    r"|crossing gates?)\b",
    re.IGNORECASE,
)
_FIRE_SERVICE = re.compile(
    r"\bfire\s+(?:trucks?|engines?|departments?|fighters?|stations?"
    r"|hydrants?|apparatus)\b",
    re.IGNORECASE,
)
_FIRE = re.compile(
    r"\b(?:caught fire|on fire|vehicle fire|in flames|ablaze|burned"
    r"|burning|flames|fire)\b",
    re.IGNORECASE,
)
_ROLLOVER = re.compile(
    r"\b(?:rollovers?|rolled over|overturn(?:ed|ing)?|flipped over"
    r"|flipped onto)\b",
    re.IGNORECASE,
)


def narrative_cues(text: str) -> List[str]:
    """Cue names present in a narrative, in NARRATIVE_CUES order.

    Injury counts only when the word is not negated in the preceding few
    words ("no injuries were reported" is not an injury cue). A later
    positive mention in the same narrative still counts.
    """
    if not text:
        return []
    found = []
    if _positive_injury(text):
        found.append("injury")
    if _ANIMAL.search(text):
        found.append("animal")
    if _VRU.search(text):
        found.append("vru")
    if _RAIL.search(text):
        found.append("rail")
    if _positive_fire(text):
        found.append("fire")
    if _ROLLOVER.search(text):
        found.append("rollover")
    return found


def _positive_injury(text: str) -> bool:
    for match in _INJURY_WORD.finditer(text):
        prefix = text[: match.start()]
        tail = [word.lower() for word in re.findall(r"\b[\w']+\b", prefix)[-6:]]
        if any(word in _NEGATION_WORDS for word in tail):
            continue
        return True
    return False


def _positive_fire(text: str) -> bool:
    # Drop "fire truck" / "fire engine" / similar before looking for fire.
    cleaned = _FIRE_SERVICE.sub(" ", text)
    return _FIRE.search(cleaned) is not None


def stratum_for(rule_reportable: bool, cues: Sequence[str]) -> str:
    """Narrative-only means the rules did not flag it and the narrative did."""
    if (not rule_reportable) and cues:
        return "narrative_only"
    return "remainder"


def _pick(rng: Any, items: Sequence[str], k: int) -> List[str]:
    """Sample k items with a partial Fisher-Yates driven by rng.random().

    random.sample() is not stable across Python versions. random() is the
    raw generator output, so the same seed yields the same draw on 3.10+.
    """
    pool = list(items)
    for i in range(k):
        j = i + int(rng.random() * (len(pool) - i))
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:k]


def stratified_ids(
    narrative_ids: Iterable[str],
    remainder_ids: Iterable[str],
    n: int,
    seed: int,
    narrative_fraction: float = NARRATIVE_ONLY_FRACTION,
) -> List[str]:
    """Return n event ids, over-representing the narrative-only stratum.

    Ids are sorted before sampling so file order cannot change the draw.
    When the narrative-only pool is smaller than its quota, every id in
    that pool is kept and the rest of the sample comes from the remainder.
    When n covers every eligible id, every id is returned.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if not 0.0 <= narrative_fraction <= 1.0:
        raise ValueError(
            f"narrative_fraction must be between 0 and 1, got {narrative_fraction}"
        )

    narrative = sorted(set(narrative_ids))
    remainder = sorted(set(remainder_ids) - set(narrative))
    total = len(narrative) + len(remainder)
    if n == 0 or total == 0:
        return []
    if n >= total:
        return sorted(narrative + remainder)

    base_rate = len(narrative) / total
    rate = max(narrative_fraction, base_rate)
    # Half-up, so the quota does not depend on bankers' rounding.
    target = int(n * rate + 0.5)
    target = max(0, min(target, n, len(narrative)))

    rng = random.Random(seed)
    if target == 0:
        chosen_narrative: List[str] = []
    elif target == len(narrative):
        chosen_narrative = list(narrative)
    else:
        chosen_narrative = _pick(rng, narrative, target)

    rest = n - len(chosen_narrative)
    if rest <= 0:
        chosen_remainder: List[str] = []
    elif rest >= len(remainder):
        chosen_remainder = list(remainder)
    else:
        chosen_remainder = _pick(rng, remainder, rest)

    return sorted(chosen_narrative + chosen_remainder)


def draft_rationale(rule_result: Dict[str, Any], cues: Sequence[str]) -> str:
    """One line a reviewer can accept or rewrite. Rules engine only."""
    reportable = bool(rule_result["rule_reportable"])
    severity = rule_result["rule_severity"]
    reasons = [str(reason) for reason in rule_result.get("matched_reasons", [])]
    flag = "Reportable" if reportable else "Not reportable"
    if reasons:
        because = "; ".join(reasons)
    elif reportable:
        because = "a reportability rule matched"
    else:
        because = "no reportability rule matched"
    line = (
        f"DRAFT from the rules engine, not a human label. "
        f"{flag} ({severity}): {because}."
    )
    if cues:
        mentioned = ", ".join(_CUE_LABELS.get(cue, cue) for cue in cues)
        if reportable:
            line += f" Narrative also mentions {mentioned}."
        else:
            line += (
                f" Narrative mentions {mentioned}; "
                f"confirm whether that changes the flag."
            )
    return " ".join(line.split())


def draft_record(
    event: Dict[str, Any],
    rule_result: Dict[str, Any],
    cues: Sequence[str],
    stratum: str,
    seed: int,
) -> Dict[str, Any]:
    """Workbench row plus a draft label. reportable follows the rules floor.

    The workbench reads the triaged-event fields. Importing this same file
    as labels pre-fills human_reportable and notes. label_status stays
    "draft" until a person reviews the row.
    """
    rationale = draft_rationale(rule_result, cues)
    reportable = bool(rule_result["rule_reportable"])
    return {
        "event_id": event["event_id"],
        "narrative": event["narrative"],
        "fields": event["fields"],
        "rule_severity": rule_result["rule_severity"],
        "rule_reportable": reportable,
        "rule_reasons": list(rule_result["matched_reasons"]),
        "summary": rationale,
        "reportable": reportable,
        "llm_reasoning": "Rules-only pre-fill. No model was called.",
        "human_reportable": reportable,
        "notes": rationale,
        "label_status": "draft",
        "narrative_cues": list(cues),
        "sample_stratum": stratum,
        "sample_seed": seed,
    }


def load_labels(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON list of labels")
    seen: Set[str] = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict) or not str(entry.get("event_id", "")).strip():
            raise ValueError(f"{path} entry {index} is missing event_id")
        event_id = str(entry["event_id"])
        if event_id in seen:
            raise ValueError(f"{path} contains duplicate event_id {event_id}")
        seen.add(event_id)
        if "human_reportable" in entry and not isinstance(entry["human_reportable"], bool):
            raise ValueError(
                f"{path} entry {event_id} has a non-boolean human_reportable"
            )
    return data


def resolve_exclusion(holdout_path: Path, labels_path: Path, rewrite_holdout: bool = False) -> Set[str]:
    """Exclusion ids are the frozen holdout.

    The first run copies labels_path onto holdout_path and excludes those
    ids. Later runs keep the holdout file as it is, including when the
    working label file has grown, unless rewrite_holdout is set.
    """
    if holdout_path.exists() and not rewrite_holdout:
        labels = load_labels(holdout_path)
        return {str(entry["event_id"]) for entry in labels}

    if not labels_path.is_file():
        raise FileNotFoundError(
            f"No holdout at {holdout_path} and no labels file at {labels_path}"
        )
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(labels_path, holdout_path)
    labels = load_labels(holdout_path)
    return {str(entry["event_id"]) for entry in labels}


def load_candidate_events(
    csv_path: str,
    config: Dict[str, Any],
    exclude_ids: Set[str],
) -> List[Dict[str, Any]]:
    """Normalize the CSV. The first row for an event id wins.

    The public SGO file lists a newer report version before older ones.
    Keeping the first row drops those repeats without reading a
    dataset-specific version column.
    """
    seen: Set[str] = set()
    events = []
    for raw in read_rows(csv_path):
        event = schema.normalize_row(raw, config)
        event_id = event["event_id"]
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        if event_id in exclude_ids:
            continue
        events.append(event)
    return events


def build_review_records(
    events: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
    n: int,
    seed: int,
    narrative_fraction: float = NARRATIVE_ONLY_FRACTION,
) -> List[Dict[str, Any]]:
    classified = []
    narrative_ids = []
    remainder_ids = []
    for event in events:
        rule_result = rules.classify_event(event, config)
        cues = narrative_cues(event.get("narrative") or "")
        stratum = stratum_for(rule_result["rule_reportable"], cues)
        classified.append((event, rule_result, cues, stratum))
        if stratum == "narrative_only":
            narrative_ids.append(event["event_id"])
        else:
            remainder_ids.append(event["event_id"])

    chosen = set(
        stratified_ids(
            narrative_ids,
            remainder_ids,
            n,
            seed,
            narrative_fraction,
        )
    )
    if len(chosen) < n:
        raise ValueError(
            f"Need {n} events to sample, but only {len(chosen)} are eligible "
            f"after exclusions ({len(narrative_ids)} narrative-only, "
            f"{len(remainder_ids)} remainder)"
        )

    records = [
        draft_record(event, rule_result, cues, stratum, seed)
        for event, rule_result, cues, stratum in classified
        if event["event_id"] in chosen
    ]
    records.sort(key=lambda record: record["event_id"])
    return records


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sample events and pre-fill rules-only draft labels for human "
            "review. Does not write ground truth."
        )
    )
    parser.add_argument(
        "--input",
        default=str(REPO_ROOT / "data" / "sgo_ads.csv"),
        help="Source CSV (default: data/sgo_ads.csv)",
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config" / "nhtsa_sgo_ads.yaml"),
        help="Dataset YAML config",
    )
    parser.add_argument(
        "--labels",
        default=str(REPO_ROOT / "tests" / "eval" / "labeled_events.json"),
        help=(
            "Existing human labels. Copied to the holdout the first time "
            "it is created. Not used as the exclusion list once the holdout "
            "exists."
        ),
    )
    parser.add_argument(
        "--holdout",
        default=str(REPO_ROOT / "tests" / "eval" / "holdout_60.json"),
        help="Frozen original labels. This is the exclusion list once it exists.",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "tests" / "eval" / "to_review.json"),
        help="Draft reviews for the workbench (default: tests/eval/to_review.json)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"How many events to draw (default: {DEFAULT_SAMPLE_SIZE})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"RNG seed (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--narrative-fraction",
        type=float,
        default=NARRATIVE_ONLY_FRACTION,
        help=(
            "Target share for narrative-only events when they are rarer "
            f"than this (default: {NARRATIVE_ONLY_FRACTION})"
        ),
    )
    parser.add_argument(
        "--rewrite-holdout",
        action="store_true",
        help="Replace an existing holdout with the current labels file. Off by default.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Input CSV not found: {input_path}", file=sys.stderr)
        print("Fetch the public file with: python fetch_data.py", file=sys.stderr)
        return 1

    try:
        config = schema.load_config(args.config)
        exclude_ids = resolve_exclusion(
            Path(args.holdout), Path(args.labels), rewrite_holdout=args.rewrite_holdout
        )
        events = load_candidate_events(str(input_path), config, exclude_ids)
        records = build_review_records(
            events, config, args.n, args.seed, args.narrative_fraction
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    output_path = Path(args.output)
    write_json(output_path, records)

    narrative_only = sum(1 for record in records if record["sample_stratum"] == "narrative_only")
    print(f"Wrote {len(records)} draft reviews to {output_path}")
    print(f"  narrative-only: {narrative_only}")
    print(f"  remainder: {len(records) - narrative_only}")
    print(f"  excluded holdout ids: {len(exclude_ids)}")
    print(f"  seed: {args.seed}")
    print(f"Holdout (frozen, not rewritten unless --rewrite-holdout): {args.holdout}")
    print(
        "These are drafts, not ground truth. Open "
        "dashboard.html?data=tests/eval/to_review.json and import this "
        "file to pre-fill the draft flag and rationale. Export only after "
        "a person has reviewed every label."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

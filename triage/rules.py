"""
Deterministic, auditable classification. Every result here is reproducible
by hand from the config file alone — no model call involved. This is the
floor the LLM layer builds on top of, never overrides downward.
"""
from __future__ import annotations

from typing import Any, Dict, List

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _field_text(event: Dict[str, Any], field: str) -> str:
    value = event["fields"].get(field, "")
    return str(value) if value is not None else ""


def _keyword_rule_matches(event: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    text = _field_text(event, rule["field"]).lower()
    contains = [s.lower() for s in rule.get("contains", [])]
    not_contains = [s.lower() for s in rule.get("not_contains", [])]

    if contains and not any(s in text for s in contains):
        return False
    if any(s in text for s in not_contains):
        return False
    return True


def _numeric_rule_matches(event: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    raw = event["fields"].get(rule["field"])
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False

    op = rule["operator"]
    threshold = rule["threshold"]
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    raise ValueError(f"Unsupported operator: {op}")


def classify_event(event: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Returns {rule_severity, rule_reportable, matched_reasons: [str]}"""
    rules_cfg = config["rules"]
    matched_reasons: List[str] = []
    best_severity = rules_cfg.get("default_severity", "low")
    reportable = rules_cfg.get("default_reportable", False)

    all_rules = [
        (r, _keyword_rule_matches) for r in rules_cfg.get("keyword_rules", [])
    ] + [
        (r, _numeric_rule_matches) for r in rules_cfg.get("numeric_rules", [])
    ]

    for rule, matcher in all_rules:
        if matcher(event, rule):
            matched_reasons.append(rule["reason"])
            if _SEVERITY_RANK[rule["severity"]] > _SEVERITY_RANK[best_severity]:
                best_severity = rule["severity"]
            if rule.get("reportable", False):
                reportable = True

    return {
        "rule_severity": best_severity,
        "rule_reportable": reportable,
        "matched_reasons": matched_reasons,
    }

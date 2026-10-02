"""
Loads a dataset config (YAML) and normalizes raw CSV rows into a small
canonical event dict. This is the only layer that needs to know about a
specific dataset's column names — everything downstream works purely off
the canonical field names defined in the config's `fields:` block.
"""
from __future__ import annotations

import yaml
from typing import Any, Dict


_VALID_SEVERITIES = {"low", "medium", "high", "critical"}
_VALID_OPERATORS = {">", ">=", "<", "<=", "=="}


def _rule_label(rule_list_name: str, index: int, rule: Dict[str, Any]) -> str:
    reason = rule.get("reason")
    if reason:
        return f'{rule_list_name}[{index}] ("{reason}")'
    return f"{rule_list_name}[{index}]"


def _validate_rules(config: Dict[str, Any], path: str) -> None:
    """Fail at load time with an error naming the offending rule, instead of
    a bare KeyError from deep inside rules.py mid-run."""
    rules_cfg = config["rules"]

    for i, rule in enumerate(rules_cfg.get("keyword_rules", [])):
        label = _rule_label("keyword_rules", i, rule)
        for key in ("field", "severity", "reason"):
            if key not in rule:
                raise ValueError(
                    f"Config {path}: {label} is missing required key '{key}'"
                )
        if "contains" not in rule and "not_contains" not in rule:
            raise ValueError(
                f"Config {path}: {label} needs at least one of "
                f"'contains' or 'not_contains'"
            )
        if rule["severity"] not in _VALID_SEVERITIES:
            raise ValueError(
                f"Config {path}: {label} has invalid severity "
                f"'{rule['severity']}' (must be one of {sorted(_VALID_SEVERITIES)})"
            )

    for i, rule in enumerate(rules_cfg.get("numeric_rules", [])):
        label = _rule_label("numeric_rules", i, rule)
        for key in ("field", "operator", "threshold", "severity", "reason"):
            if key not in rule:
                raise ValueError(
                    f"Config {path}: {label} is missing required key '{key}'"
                )
        if rule["operator"] not in _VALID_OPERATORS:
            raise ValueError(
                f"Config {path}: {label} has invalid operator "
                f"'{rule['operator']}' (must be one of {sorted(_VALID_OPERATORS)})"
            )
        if rule["severity"] not in _VALID_SEVERITIES:
            raise ValueError(
                f"Config {path}: {label} has invalid severity "
                f"'{rule['severity']}' (must be one of {sorted(_VALID_SEVERITIES)})"
            )


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    required = ["dataset_name", "id_field", "narrative_field", "fields", "rules"]
    missing = [k for k in required if k not in config]
    if missing:
        raise ValueError(f"Config {path} is missing required keys: {missing}")

    _validate_rules(config, path)
    return config


def normalize_row(row: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one raw CSV row (dict of source-column -> value) into a
    canonical event dict: {event_id, narrative, fields: {canonical_name: value}}
    """
    id_field = config["id_field"]
    narrative_field = config["narrative_field"]

    event = {
        "event_id": str(row.get(id_field, "")).strip(),
        "narrative": str(row.get(narrative_field, "")).strip(),
        "fields": {},
    }

    for canonical_name, source_column in config["fields"].items():
        raw_value = row.get(source_column, "")
        event["fields"][canonical_name] = (
            raw_value.strip() if isinstance(raw_value, str) else raw_value
        )

    return event

# Implementation Spec: Hardening the Safety Event Triage Agent

## Context
`triage/schema.py`, `triage/rules.py`, `triage/llm_client.py`,
`triage/pipeline.py`, and `main.py` already exist and work, the rules
engine has been manually validated against the full real NHTSA SGO dataset
(1,239 rows). This spec is about turning that validated prototype into a
tested, packaged, robust tool. **Do not redesign the existing architecture.**
The job is to add tests, packaging, error handling, an eval harness, and
optionally a dashboard around what's already there.

## Goals
1. A test suite that locks in current behavior as a regression baseline.
2. Proper Python packaging (installable, has a console entry point).
3. Robustness: clear errors instead of stack traces when Ollama isn't
   running, malformed config, malformed CSV rows.
4. A lightweight evaluation harness for measuring LLM-vs-rules agreement
   once I hand-label a sample.
5. (Stretch) A zero-dependency static HTML dashboard to browse results.

## Non-goals
- No cloud LLM fallback or provider abstraction, Ollama only.
- No dataset-specific logic outside `config/*.yaml`.
- No database, no web server/backend, no build tooling (webpack etc.).
- No change to the rules engine's *semantics* except where explicitly
  called out as a bug fix (see Phase 1 note on `expected_classifications.json`).

---

## Phase 1, Test suite & packaging

**`tests/test_schema.py`**
- Load `config/nhtsa_sgo_ads.yaml` and `tests/fixtures/sample_events.csv`.
- Assert `normalize_row()` produces the correct canonical field values for
  at least 3 specific rows (pick ones with distinct values across fields).
- Assert it handles a row with a blank/missing field without raising.

**`tests/test_rules.py`**
- For every row in `tests/fixtures/sample_events.csv`, run
  `schema.normalize_row()` then `rules.classify_event()`, and assert the
  result matches `tests/fixtures/expected_classifications.json` exactly
  (`rule_severity`, `rule_reportable`, and `matched_reasons` as a set
  comparison, not order-sensitive).
- This file is the regression baseline. If a test fails because you
  believe the *baseline* is wrong (not the code), stop and tell me, don't
  silently regenerate it. (One bug was already found and fixed this way:
  the "Minor" keyword rule used to match "Minor W/ Hospitalization" too;
  it now excludes that via `not_contains`. That fix is already baked into
  the current baseline, you're looking for *new* discrepancies, not that
  one.)

**`tests/test_llm_client.py`**
- Mock `requests.post` (use `unittest.mock.patch`) to test `LLMClient.triage_event()`:
  1. A well-formed response with a `submit_triage` tool call parses into
     the expected dict shape.
  2. The floor logic: when `rule_result["rule_reportable"] is True` and the
     mocked LLM response sets `recommend_reportable: false`, the final
     `llm_reportable` must still be `True`.
  3. The escalation path: `rule_result["rule_reportable"] is False` and the
     mocked response sets `recommend_reportable: true` → final is `True`.
  4. The no-tool-call fallback path (message has plain `content`, no
     `tool_calls`) returns gracefully without raising.
- Also test `StubClient` separately (trivial, but keep coverage consistent).

**`tests/test_pipeline.py`**
- Run `run_pipeline()` against `tests/fixtures/sample_events.csv` with
  `StubClient` (no network involved). Assert: row count matches the
  fixture, and severities present include at least "critical", "high",
  "medium", and "low" (i.e. the fixture's diversity actually exercises the
  pipeline end to end).

**Packaging**
- Add `pyproject.toml`: project metadata, dependencies pulled from
  `requirements.txt`, a `[project.optional-dependencies] dev = ["pytest"]`
  group, and a console script entry point: `triage-agent = "main:main"`.
- Add `.github/workflows/ci.yml`: on push and pull_request, set up Python
  3.11, `pip install -e .[dev]`, run `pytest -q`.

**Acceptance criteria for Phase 1**
- `pytest -q` passes locally, all green.
- `pip install -e .` succeeds; `triage-agent --help` runs and shows the
  existing CLI options.
- Show me the full pytest output before moving to Phase 2.

---

## Phase 2, Robustness

- In `llm_client.py`, wrap the `requests.post` call. On
  `requests.exceptions.ConnectionError`, raise a clear error:
  `"Could not reach Ollama at {base_url}, is 'ollama serve' running, and
  is the model pulled? (ollama pull {model})"`. Add one retry with a short
  backoff for other transient request exceptions (timeouts), but fail fast
  (no retry) on connection-refused, retrying that just wastes time.
- Replace `print(..., file=sys.stderr)` calls in `main.py` with the
  standard `logging` module: INFO level by default, add a `--verbose` flag
  for DEBUG. Keep the same information content, just structured.
- In `schema.load_config()`, validate the shape of each rule dict (keyword
  rules need `field`, `contains` or `not_contains`, `severity`, `reason`;
  numeric rules need `field`, `operator`, `threshold`, `severity`,
  `reason`). On a malformed rule, raise a `ValueError` naming which rule
  (by index/reason if present) and which key is missing, not a bare
  `KeyError` from deep inside `rules.py`.
- In `pipeline.read_rows()`, if a row fails to process (e.g. cannot be
  normalized for some unexpected reason), log a warning with the row
  number and skip it rather than crashing the whole run. Print a final
  count of skipped rows in the CLI summary.

**Acceptance criteria for Phase 2**
- New unit tests cover each failure path above (mock the connection
  error; feed a malformed config; feed a CSV with one bad row).
- All Phase 1 tests still pass unchanged.

---

## Phase 3, Evaluation harness

- Create `tests/eval/labeled_events.json` containing `[]`: an empty list.
  Do not invent ground-truth labels; I'll hand-label a batch of real
  events myself (schema: `{"event_id": str, "human_reportable": bool,
  "notes": str}`).
- Create `eval/run_eval.py`: loads `labeled_events.json`, runs the full
  pipeline (rules + real LLM call this time, not the stub) restricted to
  just those event IDs, and prints a small report: overall agreement rate,
  false-negative count (human said reportable, agent said not, flag this
  count prominently, it's the dangerous direction), and false-positive
  count. Exits cleanly and reports "0 labeled events, nothing to evaluate"
  if the label file is empty.

**Acceptance criteria for Phase 3**
- `python eval/run_eval.py` runs against the empty label file without
  error.
- Once I add real labels myself, re-run it and show me the report.

---

## Phase 4, Optional dashboard (stretch, only after 1–3 are done)

- `dashboard.html`: one static file, zero build step, zero dependencies.
  Reads a triaged-results JSON path from a `?data=` query parameter (e.g.
  `dashboard.html?data=output/triaged.json`), `fetch()`s it, note in a
  comment that this requires serving the folder over HTTP
  (`python -m http.server`) rather than opening the file directly, since
  `fetch()` on `file://` is blocked by browsers.
- Table view: event ID, severity (color-coded, reuse whatever's simplest,
  e.g. CSS classes per severity level), reportable flag, summary. Sortable
  by severity, filterable by reportable true/false. Plain HTML/CSS/JS in
  one file, no React, no npm, consistent with the rest of the project.

---

## Process instructions for this session (Cursor)
1. Open Agent on this repo (`Cmd/Ctrl+I`, or `Cmd+Shift+P` → "Agents
   Window" on newer Cursor versions). Cursor picks up `AGENTS.md`
   automatically from the project root.
2. Before starting Phase 1, switch to Plan mode (`Shift+Tab` from the
   agent input, or pick "Plan" from the mode dropdown next to it) and ask
   it to read `AGENTS.md`, this file, `docs/TASKS.md`, and everything
   under `triage/` and `tests/fixtures/` before writing a plan.
3. Review the plan it produces (Cursor writes it to an editable markdown
   file): adjust anything before approving.
4. Switch to Agent mode to execute the approved Phase 1 plan. Have it run
   `pytest -q` and show you the output before moving on.
5. Repeat: back to Plan mode for Phase 2, then 3, then 4. Don't let it
   pre-build a later phase while still finishing an earlier one.
6. Ask before it adds any dependency not already in `requirements.txt`.

Cursor's UI moves fast (mode menus and shortcuts have changed more than
once in 2026): if `Shift+Tab` doesn't cycle to Plan mode, check the mode
dropdown directly, or ask Cursor's own docs/help.

If you switch back to Claude Code for any of this, the same phases apply, 
just use Claude Code's plan mode instead (see `CLAUDE.md`).

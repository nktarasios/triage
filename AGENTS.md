# Safety Event Triage Agent, Agent Instructions

This is the canonical project-instructions file. Cursor reads it natively
from the project root. Claude Code reads `CLAUDE.md`, which just imports
this file, edit here, not there, so the two never drift apart.

## What this is
A local, privacy-first agent that triages tabular safety-event data (crash
reports, near-miss logs, incident feeds) into severity levels and a
regulatory-reportability flag. A deterministic rules engine does the
auditable floor-level classification; a local LLM (via Ollama) drafts
plain-language summaries and can escalate ambiguous cases based on
narrative text the structured fields miss. Fully local: no cloud API
calls, no data leaves the machine, no telemetry.

This is a personal project. The example dataset shipped here is NHTSA's
public Standing General Order (SGO) crash data, real, open, and legally
unencumbered.

## Current state (v0.1, already built and validated, do not casually rewrite)
- `triage/schema.py`: loads a YAML config, normalizes arbitrary CSV rows
  into a canonical event dict. Nothing dataset-specific lives here.
- `triage/rules.py`: deterministic keyword/numeric threshold rules ->
  severity + reportable flag. Auditable: reproducible by hand from the
  config alone.
- `triage/llm_client.py`: calls a local Ollama model via its
  OpenAI-compatible `/v1/chat/completions` endpoint, forcing a
  `submit_triage` tool call for structured output. Enforces a floor: the
  LLM may escalate a rules-engine "not reportable" event but may never
  downgrade a rules-engine "reportable" one.
- `triage/pipeline.py` / `main.py`: CLI. `--no-llm` runs rules-only, no
  Ollama required, for fast iteration.
- `config/nhtsa_sgo_ads.yaml`: the only file that knows real NHTSA column
  names and thresholds. A new dataset means a new config file, not a code
  change.
- `tests/fixtures/sample_events.csv`: 14 real rows sampled from the actual
  NHTSA dataset, chosen to cover every severity/reportability path
  (fatality, vulnerable road user, hospitalization, airbag, tow-only,
  minor injury, plain property damage, high pre-crash speed).
- `tests/fixtures/expected_classifications.json`: the current rules
  engine's output on that fixture, frozen as a regression baseline.

Read `docs/SPEC.md` for the full build spec and `docs/TASKS.md` for the
phased checklist. Work phase by phase; don't jump ahead.

## Commands
- Install: `pip install -e .[dev]`
- Fetch real demo data (full dataset, needs network): `python fetch_data.py`
- Rules-only dry run (no Ollama, no network beyond the fixture): mimic
  `main.py` against `tests/fixtures/sample_events.csv` with `--no-llm`
- Tests: `pytest -q` (57 tests across 6 files)

## Hard rules, do not violate
- Stays fully local. Never add a call to a cloud LLM API anywhere in
  `triage/` or `main.py`. Ollama only.
- Stays dataset-agnostic. Nothing outside `config/*.yaml` may hard-code
  NHTSA-specific column names, values, or thresholds.
- Public data only. No non-public datasets anywhere: code, comments,
  tests, docs, or commit messages.
- The regression baseline in `tests/fixtures/expected_classifications.json`
  must keep matching exactly, UNLESS a change is a deliberate, called-out
  bug fix to `rules.py` or the config, in that case, regenerate the
  baseline and say explicitly what changed and why, don't silently update it.
- Don't add heavyweight dependencies (web frameworks, ORMs, databases)
  without asking first. This stays a simple, inspectable CLI tool, plus at
  most one dependency-free static HTML dashboard file.

## Workflow preferences
- For anything touching more than ~2 files, use your tool's plan/preview
  mode and wait for approval before editing (Cursor: Plan mode, usually
  Shift+Tab from the agent input, or the mode dropdown. Claude Code:
  Shift+Tab twice, or `/plan`).
- Work through `docs/TASKS.md` phase by phase. Don't start a later phase
  before the current one's tests pass.
- Every new module gets a corresponding test. Run `pytest -q` before
  declaring a phase done, and show the output.
- Commit after each completed phase with a descriptive message, don't
  squash multiple phases into one commit.

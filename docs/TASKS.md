# Task Checklist

Mirrors `docs/SPEC.md`. Check items off as they're completed; keep this
file up to date so progress is visible at a glance across sessions.

## Phase 1, Test suite & packaging
- [x] `tests/test_schema.py`
- [x] `tests/test_rules.py` (matches `tests/fixtures/expected_classifications.json`)
- [x] `tests/test_llm_client.py` (mocked, incl. floor/escalation logic)
- [x] `tests/test_pipeline.py` (end-to-end with `StubClient`)
- [x] `pyproject.toml` + console entry point (`triage-agent`)
- [x] `.github/workflows/ci.yml`
- [x] `pytest -q` green (30 passed), `pip install -e .` works

## Phase 2, Robustness
- [x] Clear Ollama-unreachable error + limited retry in `llm_client.py`
- [x] `logging` module replaces `print()` in `main.py`, `--verbose` flag
- [x] Config validation with named, specific error messages
- [x] Per-row error handling in the CSV reader (skip + count, don't crash)
- [x] New tests for each of the above; Phase 1 tests still green (41 passed)

## Phase 3, Evaluation harness
- [x] `tests/eval/labeled_events.json` scaffold (empty `[]`)
- [x] `eval/run_eval.py` (agreement rate, false-negative/positive counts)
- [x] Runs cleanly against the empty label file
- [x] Re-run and reviewed once real labels are added by hand (60 events
      labeled via draft-then-human-review; results in README "Measured
      results": rules-only 95% agreement / 0 FP / 3 FN, rules+LLM 0 FN /
      41 FP)

## Phase 4, Dashboard (stretch)
- [x] `dashboard.html` (static, zero-dependency, `?data=` query param)
- [x] Sortable by severity, filterable by reportable flag
- [x] Review & labeling workbench: per-event reportable/notes labels,
      localStorage persistence, import/export in `run_eval.py`'s schema

## Phase 5, Open-source readiness
- [x] Second dataset config (`config/osha_severe_injury.yaml`) + fixture +
      tests, proving the config-not-code claim across domains
- [x] Known-limitations section in README (LLM over-escalation, local-model
      tool-calling quirk)
- [x] Dashboard screenshot in README (`docs/img/workbench.jpg`)
- [x] LICENSE, packaging metadata
- [x] Product-first README framing (problem/stakes, decision asymmetry)
- [x] `docs/DECISIONS.md` product decision log
- [x] Committed demo snapshot (`docs/demo/sample_triaged.json`) + live-demo
      link for GitHub Pages
- [ ] Enable GitHub Pages (Settings → Pages → deploy from `main`, root)
      after first push, done on github.com, not in the repo

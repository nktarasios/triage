# Roadmap: from zero misses to a feed worth reading

**Updated:** 2026-10-02

The measured state (README, "Measured results"): on 60 hand-labeled NHTSA events, rules plus
LLM escalation missed **0** reportable events but raised **41** false alarms. A feed that is
87% flagged is not triaged. Reading the model's reasoning shows why: it escalates on what
*could* have happened ("could have caused more severe damage if...") instead of what did.

**Goal:** keep false negatives at 0 and cut false alarms by at least half, measured on a
larger labeled set, with every change reproducible by one command.

## Rules for every change

1. False negatives stay at 0 on the labeled set. Any change that adds a miss is rejected.
2. The deterministic floor and escalate-only behavior do not change (`docs/DECISIONS.md` #2).
3. The frozen regression baseline does not change silently (`docs/DECISIONS.md` #5).
4. One phase per pull request. The PR description includes the before/after eval table.

---

## Phase A: Grow the labeled set from 60 to 200

**Why:** 60 events is too few to tune against without fitting to them.

- Add `eval/sample_for_labeling.py`: draw 140 more NHTSA SGO events, stratified so that
  narrative-only cases (rules say not reportable, narrative mentions injury, animal, VRU, rail,
  fire, rollover) are over-represented. Never reuse the existing 60.
- Pre-fill a draft label and a one-line rationale per event using the rules engine only.
  Output `tests/eval/to_review.json` for the workbench.
- **A human reviews every label in `dashboard.html`** (labeling standard: `docs/DECISIONS.md` #3,
  severity not fault). Export to `tests/eval/labeled_events.json`.
- Keep the original 60 as a frozen holdout: `tests/eval/holdout_60.json`.

**Acceptance:** 200 labeled events, 60 of them frozen as the holdout, sampling script
reproducible with a fixed seed.

**Status:** sampling and pre-fill only. `eval/sample_for_labeling.py` (seed
`20261002`) writes 140 rules-only drafts to `tests/eval/to_review.json` and
freezes the original 60 in `tests/eval/holdout_60.json`. Those drafts are
not labels. A person reviews every row before anything is added to
`tests/eval/labeled_events.json`.

## Phase B: Judge what happened, not what could have

**Why:** the counterfactual habit is the root cause of the false alarms.

- Rewrite the escalation instructions in `triage/llm_client.py` (`SYSTEM_PROMPT` and the
  `assess_reportability` tool schema):
  - The tool requires `evidence_quote`: a verbatim span from the narrative supporting escalation.
    No quote, no escalation (enforced in code, not only in the prompt).
  - The tool requires `trigger`: one of the configured reportability triggers (injury alleged,
    VRU involved, airbag, tow-away, fatality, rail, fire). Free-text reasons do not escalate.
  - Explicitly instruct: judge only outcomes the narrative states occurred. Hypothetical harm
    ("could have", "potential", "if") is not evidence.
- Code-side guard: reject an escalation whose `evidence_quote` is not a substring of the narrative.

**Acceptance:** on the 140 tuning events and then the 60 holdout, false negatives = 0 and
false alarms reduced by at least 50% versus the README baseline. Report both sets.

## Phase C: Per-trigger precision report

- `eval/run_eval.py --by-trigger`: precision and count per escalation trigger, plus the
  confusion matrix. Write `eval/report.md`.
- If one trigger produces most false alarms, add a config-level threshold for it in
  `config/nhtsa_sgo_ads.yaml` (not code).

**Acceptance:** `eval/report.md` regenerates in one command and shows per-trigger precision.

## Phase D: README and workbench refresh

- Update "Measured results" with the new table (rules only, rules + old prompt, rules + new
  prompt) on the holdout, and the per-trigger chart.
- Refresh `docs/img/workbench.jpg` with an escalated row that shows the evidence quote.

**Acceptance:** README numbers match `eval/report.md` exactly.

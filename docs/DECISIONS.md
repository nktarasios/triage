# Product Decision Log

The choices below are product decisions, not engineering conveniences.
Each entry records the context, the call that was made, and what it cost, 
so a future maintainer (or reviewer) can tell deliberate tradeoffs from
accidents.

The single organizing principle: **false negatives and false positives are
not symmetric.** A missed reportable event is a regulatory violation with
fines and consent orders attached; an over-flagged event costs a reviewer
a few minutes. Every decision here leans on that asymmetry.

---

## 1. Fully local, Ollama only, no cloud fallback

**Context.** Safety-event data routinely contains PII, confidential
business information, and material tied to government reporting. The
default architecture for an LLM product, send text to a cloud API, is
a non-starter for the most safety-sensitive users, who are exactly the
target users.

**Decision.** The pipeline talks only to a local Ollama endpoint. There is
no cloud provider abstraction, no fallback, and adding one is an explicit
non-goal (see `docs/SPEC.md`).

**Tradeoff.** Local models are meaningfully weaker than frontier cloud
models, the over-escalation documented in the README's Known Limitations
is partly a model-capability problem. Accepted, because "your data never
leaves the machine" is the product's core promise, and a weaker summary
is recoverable while a data leak is not.

## 2. Deterministic rules floor; the LLM may only escalate

**Context.** An LLM cannot be the sole gate on a compliance decision: it
is not auditable, not reproducible, and fails in unpredictable ways. But
structured fields miss what narratives contain, an animal struck at
speed, a vehicle driving under a closing railroad gate.

**Decision.** A deterministic, config-driven rules engine produces the
floor classification, reproducible by hand from the YAML alone. The LLM
reads the narrative and may *escalate* a not-reportable event, but can
never downgrade a rules-flagged one, the floor is enforced in code
(`triage/llm_client.py`), not in the prompt.

**Tradeoff.** The agent can only ever add review work on top of the rules,
never reduce it. That is the point: given the asymmetry above, an
escalation error is cheap and a suppression error is not.

## 3. Reportability is defined by severity, not fault

**Context.** When hand-labeling ground truth, the intuitive move is to
mark not-at-fault events (the AV rear-ended at a red light) as not
reportable. Regulators define it the other way: NHTSA's Standing General
Order triggers on outcomes, injury, airbag deployment, tow-away,
vulnerable road user, regardless of fault, because fault takes months to
adjudicate while reporting deadlines are days, and because not-at-fault
patterns (e.g. AVs being rear-ended unusually often) are themselves
safety signals.

**Decision.** Labels, rules, and eval all use the severity-based
definition. Fault belongs in the reviewer notes, not the flag.

**Tradeoff.** The reportable set includes events where the AV did nothing
wrong, which feels like noise until you remember the regulator is
watching the fleet, not assigning blame per crash.

## 4. Datasets are config files, not code

**Context.** Every safety-data pipeline dies the same death: dataset
specifics leak into the code until adopting a new feed means a rewrite.

**Decision.** Nothing outside `config/*.yaml` may reference a real column
name, value, or threshold. Proven, not asserted: the repo ships two
configs from unrelated domains, NHTSA AV crashes and OSHA workplace
severe-injury reports, running identical code, each covered by tests
against real sampled rows.

**Tradeoff.** The rules engine only offers keyword and numeric-threshold
primitives, so an exotic rule may not be expressible in YAML. Accepted
until a real dataset demands more, generality should be pulled by
examples, not pushed by speculation.

## 5. The regression baseline is frozen, and changes must be loud

**Context.** In a compliance tool, a silent change in classification
behavior is worse than a bug, it invalidates every past triage decision
without anyone noticing.

**Decision.** `tests/fixtures/expected_classifications.json` freezes the
rules engine's output on real sampled rows. CI fails on any divergence.
The baseline may only change as a deliberate, called-out fix with the
reasoning stated, never regenerated silently.

**Tradeoff.** Friction on every rules change, by design.

## 6. The dashboard is one dependency-free HTML file

**Context.** The reviewers who need to see results (safety analysts,
compliance staff) won't install a web stack, and this project stays
inspectable.

**Decision.** `dashboard.html` is a single static file: no framework, no
build step, no server beyond `python -m http.server`. It doubles as the
labeling workbench that produces eval ground truth, closing the loop from
output → human judgment → measured quality.

**Tradeoff.** No persistence beyond browser localStorage and no
multi-user support. Fine for a single-reviewer tool; a team version would
be a different product.

## 7. Rejected alternatives

- **Cloud LLM fallback**: breaks the core privacy promise (see #1).
- **A database**: a JSON file per run is inspectable, diffable, and
  sufficient at this scale.
- **A web framework for the dashboard**: see #6.
- **LLM-only classification**: see #2; also fails the "reproducible by
  hand" audit requirement.
- **Fault-based labeling**: see #3; contradicts how the regulation
  actually works.

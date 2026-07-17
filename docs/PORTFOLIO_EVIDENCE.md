# Rook Portfolio Evidence

This page separates verified engineering facts from model-performance claims
that still require an authorized live evaluation.

## Problem and implemented system

Automatically generated or manually authored Skills should not become active
because one task happened to succeed. Rook places candidates in an inactive
registry, runs isolated Baseline/Forced and Baseline/Routed pairs, normalizes
Agent traces, applies deterministic evaluators, builds ScoreCards, and records
target-specific promotion or rejection decisions with stale detection and
atomic rollback.

The existing Rook runtime supplies the interactive Agent, tools, permissions,
sessions, and context management. The EvalOps extension supplies versioned
suites, isolated workspaces and artifacts, Rook/Codex adapters, evaluators,
experiment orchestration, scoring, policy, registry, reporting, CLI surfaces,
and trace-derived quarantined candidates.

## Evidence available without a model call

| Evidence | Current result |
| --- | --- |
| Full offline core suite | 1,500+ passing tests; exact current count is recorded in `docs/ROOK_PROGRESS_SUMMARY.md` |
| Operating systems | Windows and Linux GitHub Actions matrix configured |
| RM-2 evidence suite | 12 versioned cases: 3 Direct, 3 Transfer, 3 Regression, 3 Adversarial |
| Effective control | Promoted by the deterministic Fake Agent control |
| Neutral control | Rejected because it provides no measurable uplift |
| Unsafe control | Rejected after three adversarial preservation regressions |
| Scheduled live calls | Calibration 12; Pilot 24; Formal 72 |
| External calls in the control | None |

Reproduce the control evidence:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_rm2.py tests/test_evalops_portfolio.py
```

Stage the three manual versions without activating them:

```powershell
rook skill stage --bundle evals\candidates\release-manifest-v2\effective.toml
rook skill stage --bundle evals\candidates\release-manifest-v2\neutral.toml
rook skill stage --bundle evals\candidates\release-manifest-v2\unsafe.toml
rook skill status release-manifest-v2-normalizer
```

## Live measurement contract

Do not replace the following fields with estimates. Populate them only from an
immutable report produced with explicit external-call and cost authorization.

| Metric | Required evidence | Current value |
| --- | --- | --- |
| Capability paired samples | Direct and Transfer pairs after infrastructure exclusions | Not measured |
| Baseline success rate | Passed Baseline runs / valid Baseline runs | Not measured |
| Forced-Skill success rate | Passed Forced runs / valid Forced runs | Not measured |
| Paired success uplift | Mean paired Forced-Baseline delta, plus task-stratified bootstrap 95% interval | Not measured |
| New regressions | Regression/Adversarial cases that pass Baseline and fail Candidate | Not measured |
| Median latency delta | Paired median milliseconds | Not measured |
| Token delta | Paired observed input/output tokens | Not measured |
| Cost delta | Paired observed model cost | Not measured |
| Routing precision/recall | Only from reliable `skill_loaded` identity events | Not observed for Codex |

The staged protocol is 12-call Calibration, 24-call Pilot, and 72-call Formal
(12 cases x 3 repetitions x 2 arms). Each stage requires a separate explicit
authorization and stops before the next gate. Publish the suite
fingerprint, policy fingerprint, target/model version, repetition count,
infrastructure exclusions, immutable report path, and exact authorization
state together with any metric.

Pass the Codex model explicitly with `rook eval run --model <model>` or set
`ROOK_CODEX_EVAL_MODEL` for the opt-in live smoke. The model is included in the
target fingerprint instead of relying on ignored user configuration.

## Resume-safe claim boundary

Safe now:

> Built a Codex-only Skill EvalOps framework with isolated paired experiments,
> deterministic evaluation, ScoreCards, quarantine, target-specific promotion,
> stale detection, rollback, and a cross-platform offline test gate.

Not safe until a live report exists:

> Improved real Agent task success by X% while reducing cost by Y%.

Fake Agent promotion/rejection results demonstrate control-plane correctness;
they must never be presented as real model improvement.

The version-controlled RM-2 Candidate contains only general repository rules.
Case identifiers, fixture values, semantic expected documents, and validator
paths are absent from the Candidate; the standard-library validator executes
outside the Agent workspace and is included in the suite fingerprint.

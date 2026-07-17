# Rook Portfolio Evidence

This page separates verified engineering facts from model-performance claims
that still require an authorized live evaluation.

## Problem and implemented system

Automatically generated or manually authored Skills should not become active
because one task happened to succeed. Rook Forge places Candidates in an
inactive registry, runs isolated Baseline/Forced and Baseline/Routed pairs,
normalizes Agent traces, applies deterministic evaluators, and builds
ScoreCards. The automatic gate produces eligibility only; immutable human
approval is required independently for Rook and Codex deployment, with stale,
drift, and atomic rollback protection.

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
| Authorized Calibration | 12 scheduled calls; 5 complete comparable pairs; quarantined conclusion |
| Future live schedule | New Calibration 12; Pilot 24; Formal 72 |
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

## Completed Calibration (not a Formal result)

Immutable report: `.rook/evalops/artifacts/reports/evaluation-7b656409ddb54076a36cddf7822659fd/scorecard.json`. The target was Codex CLI `0.144.1` with `gpt-5.4-mini`; the Candidate was `release-manifest-v2-normalizer@1`.

| Metric | Baseline | Forced Skill | Change |
| --- | ---: | ---: | ---: |
| Comparable-pair success, n=5 | 20% | 100% | +80pp |
| Capability success, n=3 | 0% | 100% | +100pp |
| Median latency | 107.686s | 78.188s | 27.4% lower |
| Capability median latency | 120.171s | 78.188s | 34.9% lower |
| Median tokens, complete observations n=3 | 76,914 | 90,109 | 17.2% higher |
| Preservation | — | 2/2 | 0 new regressions |
| USD cost | Not observed | Not observed | Not computable |

One infrastructure exclusion left trace completeness at 80%, so the gate concluded `quarantined (excess_infrastructure_exclusions)`. These values show that the suite detected a difference; they neither qualify the Candidate for deployment nor replace the 72-call Formal resume result.

## Formal live measurement contract

Do not replace the following fields with estimates. Populate them only from an
immutable report produced with explicit external-call and cost authorization.

| Metric | Required evidence | Current value |
| --- | --- | --- |
| Capability paired samples | Direct and Transfer pairs after infrastructure exclusions | Formal not measured |
| Baseline success rate | Passed Baseline runs / valid Baseline runs | Formal not measured |
| Forced-Skill success rate | Passed Forced runs / valid Forced runs | Formal not measured |
| Paired success uplift | Mean paired Forced-Baseline delta, plus task-stratified bootstrap 95% interval | Formal not measured |
| New regressions | Regression/Adversarial cases that pass Baseline and fail Candidate | Formal not measured |
| Median latency delta | Paired median milliseconds | Formal not measured |
| Token delta | Paired observed input/output tokens | Formal not measured |
| Cost delta | Paired observed model cost | Formal not measured |
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

> Built Rook Forge, a Skill governance control plane with isolated paired
> experiments, deterministic evaluation, ScoreCards, quarantine, automatic
> gates, target-specific human approval/deployment, stale/drift detection,
> atomic rollback, and a cross-platform offline test gate.

Not safe until a Formal report exists:

> Improved real Agent task success by X% while reducing cost by Y%.

Fake Agent promotion/rejection results demonstrate control-plane correctness;
they must never be presented as real model improvement.

The version-controlled RM-2 Candidate contains only general repository rules.
Case identifiers, fixture values, semantic expected documents, and validator
paths are absent from the Candidate; the standard-library validator executes
outside the Agent workspace and is included in the suite fingerprint.

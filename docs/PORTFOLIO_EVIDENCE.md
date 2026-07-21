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
| Sealed Formal holdout | 12 disjoint cases across six repository shapes; Candidate SHA-256 locked before execution |
| Effective control | Promoted by the deterministic Fake Agent control |
| Neutral control | Rejected because it provides no measurable uplift |
| Unsafe control | Rejected after three adversarial preservation regressions |
| Authorized Calibration | 12 scheduled calls; 5 complete comparable pairs; quarantined conclusion |
| Authorized Pilot measurement | 24/24 calls complete; 12 comparable pairs; 0 infrastructure exclusions |
| Aborted first Formal authorization | 18 calls started across the aborted run and diagnostics; no Formal result |
| Adapter v4 smoke | 2/2 calls complete; both timed out after WebSocket retries; quarantined |
| Remaining live schedule | Fresh v5 HTTP-only smoke 2, then Formal 72 with separate explicit authorization |
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

## Completed 24-call Pilot measurement (not a Formal result)

Immutable report: `.rook/evalops/artifacts/reports/evaluation-5eef9bb282934e9e8748221ce9e24e2d/scorecard.json`. The authorized run completed all 12 Baseline/Forced pairs with Codex CLI `0.144.1` and `gpt-5.4-mini`.

| Metric | Baseline | Forced Skill | Change |
| --- | ---: | ---: | ---: |
| Comparable-pair success, n=12 | 25% | 100% | +75pp |
| Capability success, n=6 | 0% | 100% | +100pp; bootstrap 95% interval [100pp, 100pp] |
| Median latency | 77.469s | 59.898s | 22.7% lower |
| Capability median latency | 80.616s | 67.852s | 15.8% lower |
| Median tokens | 49,749 | 43,350 | 12.9% lower |
| Capability median tokens | 49,749 | 52,042 | 4.6% higher |
| Preservation | — | 6/6 | 0 new regressions |
| Infrastructure / trace | — | 0 exclusions | 100% complete |
| USD cost | Not observed | Not observed | Not computable |

The calls and measurements are valid, but this run accidentally used the
Formal manifest and therefore its immutable automatic decision is
`quarantined (insufficient_valid_pairs)`: one Pilot repetition supplies six
capability pairs, while the Formal policy requires 18. Rook now has a dedicated
`pilot.toml` and `rm2-pilot.toml` boundary so future 24-call runs cannot be
evaluated against the 72-call sample threshold. Existing immutable evidence is
not relabelled or silently rescored. These Pilot values are engineering
evidence, not the final resume performance claim.

## Aborted first Formal attempt (not a Formal result)

The first authorized attempt was stopped when the evidence boundary detected a
native Windows CWD escape, an ambiguous repository-root output contract,
intermittent Codex sandbox work-directory drift, and an overly broad stream
error classification. Across the aborted run and bounded diagnostics, 18 calls
were started, 17 produced terminal process artifacts, and one was force-stopped
before an artifact. No immutable Formal report was produced and no result from
this attempt is resume eligible.

The Candidate remains frozen at SHA-256
`bb69239c1388c5d6ec4fe44d97dc1e2f7ab13544baeeeb7d73a842c3a2a5bbcf`.
Suite v5, Adapter v4, Codex CLI `0.144.6`, the 180-second boundary, strict
sandbox failure classification, and recovered-reconnect handling form the new
evidence boundary. The exact redacted record is
[`rm2-formal-readiness-2026-07-20.json`](evidence/rm2-formal-readiness-2026-07-20.json).
A fresh 2-call v4 smoke was required before requesting another 72-call Formal
authorization.

## Completed Adapter v4 smoke (failed readiness gate)

Evaluation `evaluation-c3d92efe8cc749c48f81fa7c8dab94a8` used exactly two
authorized calls. Baseline and Forced Skill both emitted four WebSocket retry
events, fell back to HTTPS, and timed out at 180 seconds without a terminal
turn. The strict gate concluded `quarantined (trace_incomplete)` with 0% trace
completeness. No Windows sandbox marker or infrastructure exclusion appeared,
and no Token or USD cost observation was complete.

This is transport evidence, not a zero-effect result: the pair is unsuitable
for measuring the Skill because neither arm reached a terminal turn. Adapter v5
uses the same authenticated ChatGPT endpoint through a controlled provider with
`supports_websockets=false`. See the redacted
[`rm2-v4-smoke-2026-07-21.json`](evidence/rm2-v4-smoke-2026-07-21.json).
A separately authorized 2-call v5 smoke must pass before Formal authorization.

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

The staged protocol is 12-call Calibration (`calibration.toml`), 24-call Pilot
(`pilot.toml`), and 72-call Formal (sealed disjoint `suite.toml`, 12 cases x 3
repetitions x 2 arms). The Formal manifest locks the Candidate content hash and
fails before an Agent call if it changes. Each stage requires a separate explicit authorization and stops before
the next gate. Publish the suite
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

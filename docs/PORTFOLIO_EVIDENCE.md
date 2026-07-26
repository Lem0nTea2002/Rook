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
| Adapter v5 HTTP-only smoke | 2/2 calls complete; terminal traces 2/2; reconnect/fallback 0; infrastructure exclusions 0 |
| Aborted Adapter v5 Formal | 32 calls started; one Forced arm timed out without a terminal trace; 40 calls not started; no Formal result |
| Adapter v6 shell remediation | Two-failure threshold, one fallback attempt, explicit exhaustion marker; offline replay complete |
| Adapter v6 bounded-recovery smoke | 2/2 terminal turns and stable exhaustion markers; 2 infrastructure exclusions; readiness failed |
| Adapter v7 offline follow-up | Explicit cwd prohibited; single-line direct Python fallback; escaped-cwd error code; live trace shape replayed |
| Adapter v7 readiness smoke | 2/2 terminal turns; 100% trace completeness; 0 infrastructure exclusions; readiness passed |
| Aborted Adapter v7 Formal | 30 calls started; host idle sleep invalidated one deadline; 42 calls not started; no Formal result |
| Adapter v8 host-sleep remediation | Windows execution-state guard plus fail-closed deadline-overrun classification; offline verified |
| Adapter v8 readiness smoke | 2/2 terminal turns; 100% trace completeness; 0 infrastructure exclusions; readiness passed |
| Aborted Adapter v8 Formal | 13 calls started; one Forced arm exhausted fallback after a post-write assertion; 59 calls not started; no Formal result |
| Adapter v9 post-write remediation | Mutation and auxiliary verification separated; deterministic evaluator retains authority; 105 focused offline tests passed |
| Adapter v9 readiness smoke | 2/2 terminal turns on the previously failing application case; 100% trace completeness; 0 infrastructure exclusions; readiness passed |
| Aborted Adapter v9 Formal | 39 calls started; the real PowerShell profile loaded in a restricted sandbox; fail-fast stopped before call 40; 33 calls not started; no Formal result |
| Invalidated Adapter v10 remediation | `codex --version` did not fully load configuration; the nested login-shell override was not admissible evidence |
| Aborted Adapter v10 readiness | Baseline failed config parsing before provider initialization; empty JSONL, 0 model requests, Forced arm not started |
| Adapter v11 profile isolation | Top-level `allow_login_shell=false`; full no-model config load passes while the invalid nested-path control fails |
| Adapter v11 readiness smoke | 2/2 processes exited 0 on the prior docs failure boundary; 100% trace completeness; 0 infrastructure exclusions or profile, Web Search, reconnect, and WebSocket markers |
| Completed Adapter v11 Formal | 72/72 calls; 36 complete pairs; Baseline 25% → Forced 100% (+75pp); median latency -16.7%; median Token -19.5%; 0 new regressions and infrastructure exclusions |
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

## Completed Adapter v5 smoke (readiness gate passed)

Evaluation `evaluation-e373ad3d6c394e88b54b67ca60523d0e` used exactly two
authorized `gpt-5.4-mini` calls through the controlled HTTP/SSE provider. Both
processes exited 0 and emitted exactly one terminal turn, with zero reconnect,
WebSocket-fallback, Windows sandbox-failure, or infrastructure-exclusion events.
Trace completeness was 100%. Baseline produced `wrong_result`; Forced Skill
passed. The run also observed 127.579s vs 99.500s latency and 65,226 vs 58,284
Tokens, but one pair is deliberately too small for a Formal effect claim.

The automatic decision is therefore correctly
`quarantined (insufficient_valid_pairs)`. This does not negate the smoke: the
readiness contract tests transport, terminal trace capture, and evaluator
execution, all of which passed. The redacted record is
[`rm2-v5-smoke-2026-07-22.json`](evidence/rm2-v5-smoke-2026-07-22.json).

## Aborted Adapter v5 Formal attempt (not a Formal result)

The separately authorized 72-call run stopped fail-closed after
`holdout-mobile` repetition 3 Forced Skill timed out at 180.140s without a
terminal turn. The Agent had five failed command executions and the hidden
validator reported `output_missing`. Thirty-two calls had started: 31 produced
process artifacts, 30 became evaluated-run records, one completed Baseline was
left outside an unfinished pair, one Forced call was force-stopped, and 40 calls
never started.

This did not reproduce the v4 transport incident. Across all 31 process
artifacts, reconnect, WebSocket fallback, top-level stream error, Windows
sandbox-failure, and Web Search counts were zero. However, the strict 100% trace
gate was already unattainable, so continuing would only spend the remaining
budget on a run that could not become Formal evidence. No ScoreCard, automatic
decision, success-rate uplift, latency delta, Token delta, regression count, or
cost metric from this partial attempt is publishable. See
[`rm2-formal-v5-attempt-2026-07-22.json`](evidence/rm2-formal-v5-attempt-2026-07-22.json).

The five failures were not a WebSocket or sandbox-start recurrence. They came
from a restricted PowerShell profile/language-mode boundary, nested quoting,
blocked method invocation, and cascading checks of an output that had never
been written. The Agent did try alternate launchers, but only after repeated
PowerShell variants and probes had consumed most of the 180-second boundary.

Adapter v6 now supplies a two-consecutive-failure prompt threshold and permits
one direct fallback attempt; another failure must produce the stable
`ROOK_SHELL_FALLBACK_EXHAUSTED` report. Normalizer v2 audits threshold, recovery,
and exhaustion and maps threshold-plus-timeout to
`codex_restricted_shell_timeout`. The original raw trace replays into these
diagnostics, but the remediation itself is offline evidence and requires a new
2-call readiness smoke. See
[`rm2-formal-v5-shell-remediation-2026-07-22.json`](evidence/rm2-formal-v5-shell-remediation-2026-07-22.json).

That Adapter v6 smoke was separately authorized for exactly two calls. Both
arms reached a terminal turn and emitted the stable exhaustion marker in less
than 85 seconds, validating bounded stop behavior. Readiness still failed:
Baseline was classified `codex_windows_sandbox_error` after a `\b` path escape
became a backspace, and Forced Skill was classified
`codex_shell_fallback_exhausted` after its direct `py -c` fallback received
literal escaped newlines. The gate was
`quarantined (excess_infrastructure_exclusions)` with 0 valid pairs, so these
results are incident evidence rather than Skill-effect evidence. See
[`rm2-v6-smoke-2026-07-22.json`](evidence/rm2-v6-smoke-2026-07-22.json).

Adapter v7 now prohibits tool-level `cwd` overrides, requires relative
forward-slash paths, rejects multiline/escaped-newline `py -c` recovery
patterns in its guidance, recognizes the live Constrained Language error, and
classifies escaped-cwd error 267 separately. The Candidate and suite remain
unchanged. This follow-up has offline replay and regression coverage; see
[`rm2-v6-smoke-remediation-2026-07-22.json`](evidence/rm2-v6-smoke-remediation-2026-07-22.json).

## Completed Adapter v7 smoke (readiness gate passed)

Evaluation `evaluation-1611cc03d158454c8121b016f1c94f2c` used exactly two
authorized `gpt-5.4-mini` calls. Both processes exited 0 with terminal turns,
100% trace completeness, and zero infrastructure exclusions, reconnect events,
shell-fallback markers, Web Search events, or Windows sandbox failures. Baseline
produced `wrong_result`; Forced Skill passed. The automatic decision remains
`quarantined (insufficient_valid_pairs)` because a one-pair smoke is not an
effect study. The redacted readiness record is
[`rm2-v7-smoke-2026-07-22.json`](evidence/rm2-v7-smoke-2026-07-22.json).

## Aborted Adapter v7 Formal attempt (not a Formal result)

The subsequent authorized run started 30 of 72 calls before Rook stopped it
fail-closed. It retained 29 process artifacts and 28 evaluated-run records; 42
calls were not started, and no experiment record, ScoreCard, report, or
promotion decision exists. One requested 180-second subprocess reported
18,983,156 ms because Windows entered System Idle sleep while the process was
active and advanced system time by 18,957,278 ms after resume. Three other runs
exhausted the bounded shell fallback. These infrastructure failures made the
strict Formal gate unattainable, so the partial results are not publishable and
will not be reused. See
[`rm2-formal-v7-attempt-2026-07-22.json`](evidence/rm2-formal-v7-attempt-2026-07-22.json).

Adapter v8 now holds a Windows execution-state guard for every EvalOps
subprocess, fails closed if it cannot establish the guard, records restore
failures as cleanup failures, and classifies timeout overruns as
`codex_timeout_deadline_overrun`. The Candidate, Normalizer, and sealed suite did
not change. This is offline remediation, not a live result; see
[`rm2-formal-v7-host-sleep-remediation-2026-07-22.json`](evidence/rm2-formal-v7-host-sleep-remediation-2026-07-22.json).
A separately authorized v8 smoke then completed exactly two calls with terminal
traces, 100% trace completeness, zero infrastructure exclusions, and no timeout
overrun. Baseline produced `wrong_result`; Forced Skill passed. This is a passed
readiness gate with one pair, not a Formal effect estimate. See
[`rm2-v8-smoke-2026-07-22.json`](evidence/rm2-v8-smoke-2026-07-22.json).
A fresh 72-call Formal was then authorized and stopped fail-closed after 13
calls started. Twelve runs produced complete terminal artifacts; one in-flight
call was stopped before artifact persistence and 59 calls were not started. The
failing Forced arm wrote the requested target, then failed an auxiliary
source-normalization assertion and emitted `ROOK_SHELL_FALLBACK_EXHAUSTED`.
Adapter v8 classified the run as an Adapter error before deterministic
evaluation, so the strict zero-exclusion contract was unattainable. No Formal
ScoreCard or resume metric exists. See
[`rm2-formal-v8-attempt-2026-07-22.json`](evidence/rm2-formal-v8-attempt-2026-07-22.json).

Adapter v9 and Normalizer v3 now distinguish an uncompleted fallback mutation
from an inconclusive check after a completed write. The former remains an
Adapter error; the latter is preserved as
`codex_post_write_verification_inconclusive` and proceeds to the deterministic
evaluator, which remains the only authority for task correctness. Prompt v15
also prohibits bundling the write and auxiliary assertions in one fallback
command. This is offline remediation, not live readiness or Formal evidence;
see [`rm2-formal-v8-post-write-remediation-2026-07-22.json`](evidence/rm2-formal-v8-post-write-remediation-2026-07-22.json).
The separately authorized v9 readiness smoke then completed exactly two calls
on the previously failing application case. Both arms produced terminal traces,
the deterministic evaluator ran for both, trace completeness was 100%, and
infrastructure exclusions were zero. Baseline was wrong and Forced Skill
passed. Its automatic `quarantined (insufficient_valid_pairs)` result is
expected for a one-pair transport/readiness gate and is not a Formal effect
estimate. See
[`rm2-v9-smoke-2026-07-24.json`](evidence/rm2-v9-smoke-2026-07-24.json).

## Completed Adapter v11 Formal

The separately authorized sealed holdout completed 72/72 calls and all 36
Baseline/Forced pairs with `gpt-5.4-mini`. Baseline passed 9/36 runs (25%;
Wilson 95% CI 13.8%–41.1%), while Forced Skill passed 36/36 (100%; Wilson 95%
CI 90.4%–100%), a paired uplift of 75 percentage points. Median latency fell
from 69.773s to 58.141s (-16.7%), median observed Token use fell from 42,436 to
34,174 (-19.5%), and median tool calls fell from 6 to 4 (-33.3%). Capability
cases improved from 0/18 to 18/18; all 18 preservation pairs passed and added
zero regressions.

All 72 processes exited 0 with one terminal turn, trace completeness was 100%,
and infrastructure exclusions plus profile, Web Search, reconnect, WebSocket,
sandbox-failure, safety, secret-leak, and isolation-leak counts were zero. The
automatic gate returned `promoted (capability_success_uplift)`, but the
measurement-only run performed no approval or deployment. USD cost and Codex
routing remain not observed. See
[`rm2-formal-v11-summary-2026-07-26.json`](evidence/rm2-formal-v11-summary-2026-07-26.json).

### Formal live measurement contract

Do not replace the following fields with estimates. Populate them only from an
immutable report produced with explicit external-call and cost authorization.

| Metric | Required evidence | Current value |
| --- | --- | --- |
| Capability paired samples | Direct and Transfer pairs after infrastructure exclusions | 18 |
| Baseline success rate | Passed Baseline runs / valid Baseline runs | 25% overall; 0% capability |
| Forced-Skill success rate | Passed Forced runs / valid Forced runs | 100% overall and capability |
| Paired success uplift | Mean paired Forced-Baseline delta, plus task-stratified bootstrap 95% interval | +75pp overall; +100pp capability (95% bootstrap interval +100pp to +100pp) |
| New regressions | Regression/Adversarial cases that pass Baseline and fail Candidate | 0 across 18 preservation pairs |
| Median latency delta | Paired median milliseconds | 69.773s → 58.141s (-16.7%) |
| Token delta | Paired observed input/output tokens | 42,436 → 34,174 (-19.5%) |
| Cost delta | Paired observed model cost | Not observed |
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

Also safe with the Formal evidence attached:

> On a sealed 72-call `gpt-5.4-mini` holdout, improved paired task success from
> 25% to 100% (+75pp), reduced median latency by 16.7% and observed Token use
> by 19.5%, with zero new regressions and zero infrastructure exclusions.

Still not safe:

> Reduced USD model cost or improved Codex routing precision/recall.

Fake Agent promotion/rejection results demonstrate control-plane correctness;
they must never be presented as real model improvement.

The version-controlled RM-2 Candidate contains only general repository rules.
Case identifiers, fixture values, semantic expected documents, and validator
paths are absent from the Candidate; the standard-library validator executes
outside the Agent workspace and is included in the suite fingerprint.

# Codex Formal hardening timeline

This document preserves the incident history that was intentionally removed
from the README. Rook's evidence contract is fail closed: a partial run is not
renamed, resumed, combined with a future attempt, or reported as a Formal
effect estimate.

## Timeline

| Stage | What happened | Remediation | Evidence outcome |
| --- | --- | --- | --- |
| Initial Formal readiness | Windows CWD escaping, ambiguous task output, sandbox-root drift, and recovered-stream classification were exposed during 18 started calls and diagnostics. | Locked task contracts, workspace boundaries, and stream classification in suite v5 / Adapter v4. | Stopped; no Formal metric. |
| Adapter v4 smoke | Both arms exhausted WebSocket reconnects and HTTPS fallback without complete terminal traces. | Adapter v5 selects HTTP/SSE before process start and keeps JSONL strict. | 0/2 readiness; no effect claim. |
| Adapter v5 smoke | Exactly 2/2 calls reached one terminal event per arm with zero transport/sandbox exclusions. | Transport considered ready. | Readiness only: Baseline wrong, Forced passed. |
| Adapter v5 Formal | A Forced arm timed out after 180 seconds and five command failures. Thirty-two calls started; 40 were not started. | Replayed the trace and introduced a two-failure restricted-shell threshold, one direct fallback, stable diagnostics, and explicit exhaustion. | Stopped; no ScoreCard or Formal metric. |
| Adapter v6 smoke | Both arms terminated with the bounded exhaustion marker instead of silently timing out, but Windows `\b` path escaping and multiline `py -c` quoting failed. | Adapter v7 forbids tool-level CWD overrides, requires forward-slash relative paths, and constrains direct Python fallback to one physical line. | Bounded stop proved; readiness failed. |
| Adapter v7 smoke | Exactly 2/2 calls completed with 100% trace completeness and zero infrastructure exclusions. | Adapter ready for a fresh run. | Readiness only. |
| Adapter v7 Formal | One subprocess crossed a Windows system-idle sleep interval; three more exhausted the bounded shell fallback. Thirty calls started; 42 were not started. | Adapter v8 holds a Windows execution-state guard and detects deadline overruns. | Stopped; no Formal metric. |
| Adapter v8 smoke | Exactly 2/2 calls completed, with no infrastructure exclusion or deadline-overrun marker. | Host deadline boundary considered ready. | Readiness only. |
| Adapter v8 Formal | One Forced arm wrote the required file, then an auxiliary assertion failed and was classified as fallback exhaustion. Thirteen calls started; 59 were not started. | Adapter v9 separates required mutation from auxiliary verification; a completed write reaches the hidden deterministic evaluator. | Stopped; no Formal metric. |
| Adapter v9 smoke | The previously failing application case completed exactly 2/2 terminal calls, with 100% trace completeness and zero infrastructure exclusions. | No further Adapter change required by readiness. | Readiness passed; one pair remains below effect-policy sample size. |
| Adapter v9 Formal | The first 38 calls formed 19 complete pairs. Call 39 loaded the real PowerShell profile inside the restricted Windows sandbox; the profile failed under its language mode and the process exited without an admissible terminal result. | Add an explicit no-login-shell Codex configuration boundary. | Fail-fast stopped before call 40; 33 calls were not started; the partial ScoreCard is not a Formal metric. |
| Adapter v10 offline remediation | `codex --version` accepted `permissions.allow_login_shell=false`, but that command did not fully deserialize configuration. | Full configuration loading must be part of offline validation. | Invalidated by the live readiness parse failure; no readiness claim. |
| Adapter v10 smoke | The Baseline CLI process rejected the nested override before provider initialization; its JSONL was empty and no model request started. | Adapter v11 uses the top-level `allow_login_shell=false` field accepted by Codex 0.144.6. | Fail-fast stopped after 1/2 planned arms; the Forced arm was not started; no readiness or Formal metric. |
| Adapter v11 offline remediation | The invalid nested path fails `features list`, while `codex -c allow_login_shell=false features list` fully loads configuration and exits 0. `rook eval doctor` now validates the full immutable EvalOps config the same way. | Require a fresh, separately authorized two-call readiness on the same `holdout-docs` pair. | Offline only; no model call and no readiness claim. |
| Adapter v11 smoke | Both processes exited 0 on the prior `holdout-docs` failure boundary, each emitted one terminal turn, and trace completeness was 100%. Profile, Web Search, reconnect, WebSocket, sandbox-failure, and infrastructure-exclusion markers were all zero. | Profile isolation is ready for a fresh Formal authorization. | Readiness passed; one pair remains below the effect-policy sample size and is not a Formal result. |
| Adapter v11 Formal | A separately authorized fresh run completed 72/72 processes and 36/36 pairs. All processes exited 0, trace completeness was 100%, and infrastructure exclusions plus profile, Web Search, reconnect, WebSocket, sandbox, safety, secret, and isolation markers were zero. | No further transport or execution remediation is required for this evidence boundary. | Gate promoted on capability success uplift; measurement-only, so no approval or deployment occurred. |

## Evidence index

- [Initial readiness incident](../evidence/rm2-formal-readiness-2026-07-20.json)
- [Adapter v4 smoke](../evidence/rm2-v4-smoke-2026-07-21.json)
- [Adapter v5 smoke](../evidence/rm2-v5-smoke-2026-07-22.json)
- [Adapter v5 Formal attempt](../evidence/rm2-formal-v5-attempt-2026-07-22.json)
- [Restricted-shell remediation](../evidence/rm2-formal-v5-shell-remediation-2026-07-22.json)
- [Adapter v6 smoke](../evidence/rm2-v6-smoke-2026-07-22.json)
- [Adapter v7 follow-up](../evidence/rm2-v6-smoke-remediation-2026-07-22.json)
- [Adapter v7 smoke](../evidence/rm2-v7-smoke-2026-07-22.json)
- [Adapter v7 Formal attempt](../evidence/rm2-formal-v7-attempt-2026-07-22.json)
- [Windows host-sleep remediation](../evidence/rm2-formal-v7-host-sleep-remediation-2026-07-22.json)
- [Adapter v8 smoke](../evidence/rm2-v8-smoke-2026-07-22.json)
- [Adapter v8 Formal attempt](../evidence/rm2-formal-v8-attempt-2026-07-22.json)
- [Post-write remediation](../evidence/rm2-formal-v8-post-write-remediation-2026-07-22.json)
- [Adapter v9 smoke](../evidence/rm2-v9-smoke-2026-07-24.json)
- [Adapter v9 Formal attempt](../evidence/rm2-formal-v9-attempt-2026-07-24.json)
- [Invalidated Adapter v10 profile remediation](../evidence/rm2-formal-v9-profile-isolation-remediation-2026-07-26.json)
- [Adapter v10 smoke attempt](../evidence/rm2-v10-smoke-attempt-2026-07-26.json)
- [Adapter v11 profile isolation remediation](../evidence/rm2-formal-v10-profile-isolation-remediation-2026-07-26.json)
- [Adapter v11 smoke](../evidence/rm2-v11-smoke-2026-07-26.json)
- [Adapter v11 Formal](../evidence/rm2-formal-v11-summary-2026-07-26.json)

## Current boundary

Adapter v11 passed a fresh readiness gate and then completed a separately
authorized 72-call Formal from call 1. The Formal report is resume-eligible for
success, latency, and observed Token claims. USD cost and Codex routing remain
not observed. The measurement-only gate did not create approval or deployment
state.

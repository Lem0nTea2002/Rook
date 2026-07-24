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

## Current boundary

Adapter v9 is transport/execution ready on the two-call gate. It has not
produced a 72-call Formal report. The valid 24-call Pilot remains useful
engineering evidence, but final resume success-rate, latency, and Token claims
remain unfilled until a separately authorized Formal run completes without an
infrastructure exclusion.

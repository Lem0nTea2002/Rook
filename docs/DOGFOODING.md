# Rook Forge Dogfooding and Incident Ledger

This ledger separates real model evidence from deterministic control-plane
evidence. A row is not called a real Skill deployment unless a real Agent ran
the exam and an immutable approval/release record exists.

| ID | Scope | Evidence | Outcome | Claim boundary |
| --- | --- | --- | --- | --- |
| DF-001 | Packaged offline lifecycle | `rook eval demo` | v1 and v2 pass, deploy independently to Fake Rook/Fake Codex, then atomically roll back to v1 | Deterministic control-plane evidence only |
| DF-002 | Effective/neutral/unsafe controls | `tests/test_evalops_portfolio.py` | Effective promoted, neutral rejected, unsafe blocked for new regressions | Deterministic gate evidence only |
| DF-003 | RM-2 live Pilot | `docs/evidence/rm2-pilot-summary.json` | 24/24 real Codex calls, 12 comparable pairs, 0 infrastructure exclusions, 100% trace completeness | Real Pilot measurement; measurement-only, not deployed, not Formal |
| DF-004 | Adapter v5 HTTP-only readiness smoke | `docs/evidence/rm2-v5-smoke-2026-07-22.json` | 2/2 real Codex calls reached terminal turns with zero reconnect/fallback and zero infrastructure exclusions; Baseline wrong, Forced Skill passed | Real readiness evidence; one pair, not deployed, not Formal |
| INC-001 | Native Windows Codex sandbox | `docs/EVALOPS.md` and adapter regression tests | Split temp-root and in-process patch failures reproduced; 2/2 strict smoke and 24/24 Pilot verified the fix | Real infrastructure incident; not a Skill-effect claim |
| INC-002 | First RM-2 Formal readiness attempt | `docs/evidence/rm2-formal-readiness-2026-07-20.json` | Aborted after CWD, task-contract, sandbox-drift, and stream-classification defects; Candidate unchanged; suite v5 and Adapter v4 hardened | Real infrastructure/evidence incident; no Formal or deployment claim |
| INC-003 | Adapter v4 live smoke | `docs/evidence/rm2-v4-smoke-2026-07-21.json` | Both arms timed out after four WebSocket retries and HTTPS fallback; gate quarantined incomplete traces; Adapter v5 selects HTTP/SSE up front | Real transport incident; no Formal or Skill-effect claim |
| INC-004 | Adapter v5 Formal attempt | `docs/evidence/rm2-formal-v5-attempt-2026-07-22.json` | Stopped after one Forced arm timed out without a terminal trace; 32 calls started, 31 process artifacts, 40 not started; transport/sandbox markers 0 | Real fail-closed Agent-timeout incident; no ScoreCard, Formal, deployment, or Skill-effect claim |
| INC-005 | Restricted PowerShell recovery | `docs/evidence/rm2-formal-v5-shell-remediation-2026-07-22.json` | Replayed all 10 command results and 5 failures; added a two-failure threshold, one fallback attempt, stable diagnostics, and explicit exhaustion feedback | Offline remediation precursor to the later Adapter v6 smoke |
| INC-006 | Adapter v6 bounded-recovery smoke | `docs/evidence/rm2-v6-smoke-2026-07-22.json` | Exactly 2 real calls reached terminal turns and emitted the stable exhaustion marker; Baseline hit Windows path escape error 267 and Forced hit direct Python quoting failure | Real bounded-stop evidence; readiness failed, no Formal or Skill-effect claim |
| INC-007 | Adapter v6 smoke follow-up | `docs/evidence/rm2-v6-smoke-remediation-2026-07-22.json` | Adapter v7 forbids explicit tool cwd, constrains direct Python fallback quoting, adds a specific escaped-cwd code, and replays the live failure shape | Offline remediation precursor to the later passing v7 readiness smoke |
| DF-005 | Adapter v7 readiness smoke | `docs/evidence/rm2-v7-smoke-2026-07-22.json` | Exactly 2 real calls reached terminal turns with 100% trace completeness and zero infrastructure exclusions; Baseline wrong, Forced Skill passed | Real readiness evidence; one pair, not deployed, not Formal |
| INC-008 | Adapter v7 Formal host-sleep attempt | `docs/evidence/rm2-formal-v7-attempt-2026-07-22.json` | Stopped after 30 calls started; 29 process artifacts and 28 evaluated-run records retained; Windows system-idle sleep invalidated one deadline; 42 calls not started | Real fail-closed infrastructure incident; no ScoreCard, Formal, deployment, or Skill-effect claim |
| INC-009 | Windows host-sleep remediation | `docs/evidence/rm2-formal-v7-host-sleep-remediation-2026-07-22.json` | Adapter v8 holds a Windows execution-state guard and classifies deadline overruns as infrastructure errors; focused offline tests passed | Offline remediation precursor to the later passing v8 readiness smoke |
| DF-006 | Adapter v8 readiness smoke | `docs/evidence/rm2-v8-smoke-2026-07-22.json` | Exactly 2 real calls reached terminal turns with 100% trace completeness, zero infrastructure exclusions, and no deadline-overrun marker; Baseline wrong, Forced Skill passed | Real readiness evidence; one pair, not deployed, not Formal |
| INC-010 | Adapter v8 Formal fallback exhaustion | `docs/evidence/rm2-formal-v8-attempt-2026-07-22.json` | Stopped after 13 calls started; 12 terminal artifacts retained; one Forced arm wrote the target, then failed an auxiliary assertion and emitted the stable exhaustion marker; 59 calls not started | Real fail-closed Agent-recovery incident; no ScoreCard, Formal, deployment, or Skill-effect claim |
| INC-011 | Adapter v9 post-write remediation | `docs/evidence/rm2-formal-v8-post-write-remediation-2026-07-22.json` | Required mutation is separated from auxiliary verification; completed writes with inconclusive checks reach the deterministic evaluator while real write failures still fail closed | Offline remediation only; new 2-call readiness authorization required |
| DF-007 | Adapter v9 post-write readiness smoke | `docs/evidence/rm2-v9-smoke-2026-07-24.json` | Exactly 2 real calls on the previously failing application case reached terminal turns with 100% trace completeness and zero infrastructure exclusions; Baseline wrong, Forced Skill passed | Real readiness evidence; one pair, not deployed, not Formal |
| DF-008 | Local approval, dual deployment, drift, and rollback | `docs/evidence/forge-lifecycle-2026-07-24.json` | Four immutable approvals, four deploy releases, detected and remediated Codex file drift, then two transactional rollbacks restored v1 | Real local Registry/filesystem lifecycle with deterministic Fake-Agent exam; not a model-effect claim |
| DF-009 | Two real-repository holdouts | `docs/REAL_REPO_HOLDOUTS.md` | Two immutable Candidate hashes, two public repositories, four Direct/Regression/Adversarial cases, pinned provenance, hidden validators, and disabled network | Pre-live staged boundary, superseded by DF-010 |
| DF-010 | Two real-repository live holdouts | `docs/evidence/real-repo-live-holdouts-2026-07-27.json` | 16/16 Codex calls, 8 valid pairs, 100% trace completeness, 0 infrastructure exclusions; both Candidates rejected for new regressions | Real negative model evidence; no promotion or deployment |
| DF-011 | Formal decision approval and Codex deployment | `docs/evidence/rm2-formal-release-2026-07-27.json` | Rebuilt the redacted Formal ScoreCard from 72 terminal artifacts, recorded the decision, approved and atomically deployed v1, detected controlled drift, and restored the exact Candidate hash | Real Formal gate plus human approval and local Codex deployment; no successful rollback claim because no older approved version exists |
| DF-012 | Rook Coding Agent live dogfood | `docs/evidence/rook-coding-dogfood-2026-07-27.json` | Five isolated real coding tasks with deterministic validators: 3 passed, 2 failed; 66 model calls and 1,028,297 observed Tokens | Real Rook/DeepSeek dogfood; not a Skill A/B result |
| DF-013 | Candidate v5 successor Formal and release | `docs/evidence/rm2-v5-formal-release-2026-07-27.json` | 72/72 Adapter v12 calls; Baseline 25% → Forced 94.4% (+69.4pp); v5 independently approved and deployed; audit repair exercised v5→v1→v5 | Real model gate, real successor deployment, and real transactional rollback; USD cost and Codex routing not observed |
| DF-014 | Candidate v5 two-repository holdout | `docs/evidence/rm2-v5-two-repo-holdout-2026-07-27.json` | 24/24 Codex calls, 12 valid pairs, 100% trace completeness, 0 infrastructure exclusions; Baseline 33.3% → Forced 91.7% (+58.3pp), capability uplift +87.5pp, 0 new regressions | Real paired model evidence; measurement-only; median latency and Token increased, USD cost/routing not observed |
| INC-012 | Rook Skill routing and context amplification | `tests/test_skill_router.py` and `tests/test_agent_skill_flow.py` | Runtime reminders no longer create user turns; global metadata requires a distinctive name/trigger signal; global catalog text is removed from the prompt; active Skills are bounded, deduplicated, and task-cleared | Offline root-cause remediation; the original 3/5 live result remains unchanged until a separately authorized rerun |
| DF-015 | Ten-task Rook coding rerun | `docs/evidence/rook-coding-dogfood-v2-2026-07-27.json` | 9/10 deterministic validators passed; 106 Provider calls and 738,729 observed Tokens within the authorized 200-call ceiling; unrelated Skill selections 0/10 | Real Rook/DeepSeek dogfood; not a Skill A/B; v1/v2 task sets differ, so efficiency change is directional |
| DF-016 | GitHub PR gate | `.github/workflows/rook-forge-pr-gate.yml` | Strict Candidate/Suite/Policy/provenance checks, Candidate hash locks, focused regressions, stable JSON fingerprint, and artifact upload with external calls/costs disabled | Local command and remote PR #16 check passed with external calls/costs disabled |
| INC-013 | Dogfood provider-limit terminal handling | `rook_agent/agent/loop.py` and `benchmark/local_pytest/runner.py` | Task 8 reached the 20-call ceiling; Todo self-check exposed an uncaught limit and the runner lost its aggregate summary. The loop now emits a stable terminal response and the runner atomically persists each completed row | The failed validator remains failed and was not retried; focused verification: 82 passed, Ruff green |

Current honest count: Candidate v5 has a completed real-model Formal gate,
human-approved successor deployment, a verified v5→v1→v5 transaction chain,
and a separate positive two-repository holdout. Two independent Candidates
were also live-tested and correctly rejected. Rook itself passed 9/10 live
coding tasks with zero unrelated Skill selections; the one failure and the
provider-limit runner incident remain in the public record. The original 3/5
run used a different task set, so the observed success/efficiency changes are
not reported as a causal paired improvement.

## Required record for the next real Skill

Every new entry must include:

- immutable Candidate content hash;
- suite, policy, target, Adapter, and Normalizer fingerprints;
- external-call and model-cost authorization;
- valid/incomplete/infrastructure-excluded pair counts;
- automatic gate, human approval, deployment, drift, and rollback IDs;
- redacted report hash and reproduction command;
- incident and remediation reason codes without prompts, credentials, or raw secrets.

## Reproduction

```powershell
rook eval demo
rook eval trends release-manifest-v2-normalizer --agent codex
```

The first command is offline and zero cost. The second reads existing redacted
reports only; it does not launch an Agent or call a model.

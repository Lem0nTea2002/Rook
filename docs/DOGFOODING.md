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

Current honest count: one Skill has real model measurement, while zero Skills
have a real-model gate plus human-approved production deployment. The packaged
demo covers approval, drift, deployment, and rollback deterministically. This
distinction remains visible until additional real dogfooding is authorized.

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

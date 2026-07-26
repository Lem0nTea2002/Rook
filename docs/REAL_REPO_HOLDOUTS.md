# Real-repository Skill holdouts

Rook keeps these holdouts separate from the synthetic RM-2 transport/effect
suite. They exercise different Skill categories on immutable snapshots from
two public repositories. Every source commit, selected blob, transformation,
Candidate rendering hash, hidden validator, and disabled-network policy is
version controlled.

The Candidates remain `quarantined`. On 2026-07-27, both suites also completed
an authorized live content-family exam with Codex CLI 0.144.6 and
`gpt-5.4-mini`: 16/16 calls completed, all four pairs per Skill were comparable,
trace completeness was 100%, and infrastructure exclusions were zero. Both
Candidates were rejected for new regressions. This is valid negative evidence,
not a Skill-effect improvement claim.

## 1. GitHub Actions CI guard

| Field | Value |
| --- | --- |
| Skill | `github-actions-ci-guard@1` |
| Candidate SHA-256 | `8ee402c62e30a4b54faca8976e8d730721ff7297b832602015dc1b32871dc8f3` |
| Source | [ZHUMUJUN/Rook](https://github.com/ZHUMUJUN/Rook) |
| Pinned commit | `94e866a50f60d1ff3dca9102b7031e2cfd71b4a9` |
| Cases | Harden a real offline CI workflow; preserve a real Dependabot file |
| Categories | Direct + Regression |
| Network | Disabled |

The fixture copies the selected source files byte-for-byte. `PROVENANCE.json`
records their Git blob SHA-1 values. The hidden validator requires bounded job
timeouts, least-privilege permissions, checkout credential isolation, the
original OS/Python matrix, and Rook's no-external-evaluation/no-cost controls.
The regression case rejects unnecessary edits to non-workflow YAML.

## 2. RAG evidence reporter

| Field | Value |
| --- | --- |
| Skill | `rag-evidence-reporter@1` |
| Candidate SHA-256 | `f14946d8c195f4335ff48154b51d8f0a34a9e1e8b10eefc6518b6bc74b48feed` |
| Source | [Multimodal LLM Agent for Scientific Document RAG](https://github.com/ZHUMUJUN/Multimodal-LLM-Agent-for-Scientific-Document-RAG) |
| Pinned commit | `656c23d61a3324944a89736c82d5fba4dbea59e6` |
| Cases | Compare retrieval reports; preserve a skipped Ragas result |
| Categories | Direct + Adversarial |
| Network | Disabled |

The fixture contains only selected benchmark identity and summary objects; raw
prompts, answers, credentials, and unrelated repository data are excluded.
`PROVENANCE.json` records the original Git blobs, the transformation, and each
fixture SHA-256. The hidden validator prevents cross-dataset averaging,
invented quality/cost claims, and treating unavailable metrics as zero.

## Reproduce the deterministic boundary

```powershell
python -m pytest tests/test_evalops_real_repo_holdouts.py -q
```

The test locks Candidate hashes, parses both suites with the strict loader,
checks disabled network policy and pinned provenance, and executes reference
outputs through the same hidden validators. It makes no model call.

## Live result

| Skill | Baseline | Forced Skill | New regressions | Gate |
| --- | ---: | ---: | ---: | --- |
| `github-actions-ci-guard@1` | 2/4 | 0/4 | 2 | `rejected (new_regression)` |
| `rag-evidence-reporter@1` | 1/4 | 0/4 | 1 | `rejected (new_regression)` |

The immutable redacted summary is
[`docs/evidence/real-repo-live-holdouts-2026-07-27.json`](evidence/real-repo-live-holdouts-2026-07-27.json).
It records Candidate, Suite, Policy, target and ScoreCard fingerprints, report
hashes, success/latency/Token metrics, and the exact decision IDs. Dollar cost
remains `not observed`.

## Promotion boundary

The two Skills intentionally stop before approval:

```text
real repository snapshot
  -> immutable quarantined Candidate
  -> strict suite + hidden deterministic validator
  -> live Baseline/Forced exam (8 calls per Skill)
  -> ScoreCard / gate (both rejected for new regressions)
  -> approval / deployment (correctly blocked)
```

This shows that a passing reference fixture does not guarantee a useful Skill:
Rook still requires paired live evidence and blocks deployment when the
Candidate harms preservation cases.

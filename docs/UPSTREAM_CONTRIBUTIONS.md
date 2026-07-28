# Live upstream contribution track

Rook's historical full-repository catalog is not evidence of new upstream
contributions. This track records current Issues separately and fails closed
when a task is already claimed, requires a design decision, becomes stale, or
cannot be explained and reviewed by the human contributor.

## Batch lifecycle

```text
screen current Issues
  -> reject duplicates and design-blocked tasks
  -> human reads policy, Issue, and proposed scope
  -> human posts the repository-required claim
  -> reproduce on the pinned full repository
  -> RED test
  -> minimal patch
  -> official project validation
  -> Rook gate
  -> human reviews and explains every change
  -> fork branch
  -> human submits and handles upstream discussion
  -> record accepted, rejected, withdrawn, or superseded outcome
```

The first batch is intentionally capped at one task in each of three
repositories. The queue must not expand to 20–30 tasks until these three tasks
have exercised the real claim, review, submission, and outcome-recording
workflow.

## Batch 1

| Repository | Issue | Current state | Eligible scope |
| --- | --- | --- | --- |
| pytest | [#14771](https://github.com/pytest-dev/pytest/issues/14771) | Draft PR [#14789](https://github.com/pytest-dev/pytest/pull/14789) submitted | Document the filesystem boundary for frozen tests; an archive Collector is excluded without maintainer design approval |
| scikit-learn | [#13762](https://github.com/scikit-learn/scikit-learn/issues/13762) | Draft PR [#34587](https://github.com/scikit-learn/scikit-learn/pull/34587) submitted | Replace the skipped ARM-unstable doctest with an architecture-stable example |
| Sphinx | [#6689](https://github.com/sphinx-doc/sphinx/issues/6689) | Draft PR [#14558](https://github.com/sphinx-doc/sphinx/pull/14558) submitted manually | Add an inline todo role, domain/todolist integration, tests, docs, and change note |

The machine-readable snapshot is
[`upstream-contribution-batch-1-2026-07-27.json`](evidence/upstream-contribution-batch-1-2026-07-27.json).
It includes duplicate-task rejections, clone failures and recovery, empty
withdrawal history, pinned repository heads, and the external-claim boundary.
The append-only event history is
[`upstream-contribution-ledger-2026-07-27.jsonl`](evidence/upstream-contribution-ledger-2026-07-27.jsonl).
Its 26 events preserve the two recovered clone failures, three selected tasks,
three human-claim gates, three screening rejections, the three human-authored
claims, branch creation, locally validated review gates, and three completed
human reviews plus three human-owned Draft PRs in one verified SHA-256 hash
chain.

The
[`upstream-contribution-review-packet-2026-07-27.md`](evidence/upstream-contribution-review-packet-2026-07-27.md)
records each prepared diff's scope, exact validation, environment limitations,
and the questions that the human contributor must answer before submission.

Verify the chain or inspect one task without network or model calls:

```powershell
rook repo contribution-history `
  --ledger docs/evidence/upstream-contribution-ledger-2026-07-27.jsonl

rook repo contribution-history `
  --ledger docs/evidence/upstream-contribution-ledger-2026-07-27.jsonl `
  --task-id sklearn-13762 `
  --json
```

Append an event only after its evidence exists:

```powershell
rook repo contribution-record `
  --ledger docs/evidence/upstream-contribution-ledger-2026-07-27.jsonl `
  --task-id sklearn-13762 `
  --repository https://github.com/scikit-learn/scikit-learn `
  --issue-url https://github.com/scikit-learn/scikit-learn/issues/13762 `
  --status claimed `
  --actor human:Lem0nTea2002 `
  --reason-code human_comment `
  --evidence https://github.com/scikit-learn/scikit-learn/issues/13762#issuecomment-REPLACE
```

The example is intentionally incomplete: replace the evidence URL only with the
real comment created and reviewed by the human contributor. The ledger rejects
invalid transitions, changed repository identity, terminal-state mutation,
unsafe evidence references, unknown fields, broken sequences, and altered
event hashes.

## Human-review gate

These repositories allow or discuss AI assistance under different conditions,
but all require meaningful human responsibility. Rook therefore does not post
claim comments, impersonate the contributor, or submit upstream PRs
unattended.

Before implementation, the contributor must:

1. read the full Issue and repository contribution rules;
2. verify that the Issue still has no assignee or open competing PR;
3. describe the intended behavior and validation in their own words;
4. post the required claim or scope-confirmation comment themselves;
5. confirm that they can explain, review, test, and maintain the resulting
   change.

Before submission, the contributor must review the complete diff, reproduce
RED and GREEN results, write or materially rewrite all upstream communication,
and disclose AI assistance as required by the target repository.

Batch 1 has completed the claim, local implementation, validation, and human
review stages. Each patch has an isolated local commit and a fork branch under
the claiming account. All three patches are accurately recorded as submitted
Draft PRs. The Sphinx PR was created manually by the human contributor, as
required by that repository's policy.

## Outcome records

Every attempted task keeps one terminal or waiting state:

- `accepted`: upstream merged the human-submitted PR;
- `reviewed`: the human contributor reviewed the complete diff and validation,
  but no pull request has been submitted;
- `submitted`: a human-submitted PR is awaiting review;
- `rejected`: screening, gate, or upstream review rejected the change;
- `withdrawn`: the contributor withdrew a valid submission with a recorded
  reason;
- `superseded`: another contribution landed first;
- `blocked`: infrastructure or an explicit maintainer decision prevents safe
  progress;
- `awaiting_human_claim`: implementation has not started because repository
  interaction is still required.

No state is inferred from model output, and a prepared local patch is never
reported as an upstream PR.

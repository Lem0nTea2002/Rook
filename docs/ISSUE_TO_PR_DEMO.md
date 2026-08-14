# Issue → Reviewed Draft PR Demo

This demo turns Rook's application story into one short, reproducible workflow:

```text
Issue
  → isolated local Git repository
  → Rook plan
  → bounded code change
  → real test command
  → path / secret / regression gate
  → immutable contribution ledger
  → human review
  → Draft PR bundle
```

Run it without configuring a Provider:

```sh
rook repo issue-pr-demo \
  --approver "your-name" \
  --output .rook/issue-pr-demo
```

The command refuses to overwrite an existing output directory. A successful run
creates:

| Artifact | Purpose |
| --- | --- |
| `issue.json` | Structured input Issue and acceptance criteria |
| `repository/` | Real local Git repository on `rook/issue-1` |
| `plan.md` | Bounded implementation plan |
| `agent.patch` | Reviewable repository diff |
| `test-output.txt` | Actual unittest output |
| `gate.json` | Changed-path, test, and secret-scan decision |
| `contributions.jsonl` | Hash-chained lifecycle through human review |
| `pull-request.md` | Review-ready Draft PR description |
| `manifest.json` | Stable summary and SHA-256 evidence map |

## Evidence boundary

The demo makes **zero model calls** and performs **no GitHub write**. It proves
the application workflow and governance contract, not autonomous coding
quality. Live Rook-only task runs are measured separately.

The last step is intentionally human-owned. After inspecting the patch, tests,
gate, and PR body, the contributor may explicitly create a Draft PR and then
record the resulting URL in the contribution ledger.

## Two-minute interview walkthrough

1. Show the Rookie mascot and run the command.
2. Open `issue.json` and `plan.md` to establish the task contract.
3. Show `agent.patch` beside `test-output.txt`.
4. Explain why `gate.json` checks paths and secrets before review.
5. Verify the final ledger event is `reviewed`, not falsely `submitted`.
6. Open `pull-request.md` and explain that GitHub publication is a separate,
   explicit action.

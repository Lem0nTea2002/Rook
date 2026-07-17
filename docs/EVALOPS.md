# Rook Forge: Codex-only Skill Governance

Rook Forge evaluates a stored Skill candidate with isolated Baseline, Forced Skill, and Routed Skill runs, applies an automatic gate, waits for immutable human approval, and then deploys independently to the in-process Rook runtime or the current repository's Codex Skill directory. The implementation package remains `evalops`; Claude Code is not part of this release.

```text
Candidate quarantine -> paired exam -> ScoreCard -> automatic gate
  -> human approval per target -> deploy -> stale/drift check -> rollback
```

`promoted` means **eligible for approval**, not active. Safety failures, secret leaks, new regressions, stale evidence, and content-hash mismatches cannot be overridden by an approver.

## Deterministic demo

The version-controlled demo suite contains Direct, Transfer, Regression, and Adversarial cases:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo.py
```

The default test path uses `FakeAgentAdapter`. It does not launch Codex, call a model API, or create model charges. The demo exercises Candidate storage, paired A/B runs, ScoreCard construction, automatic gate history, human approval, Rook/Codex deployment, immutable release history, and rollback.

## CLI

Probe the local adapters without making a model call:

```powershell
rook eval doctor
```

Stage a manually authored, strict TOML bundle. Staging is offline: the bundle is
stored with `imported` origin and `quarantined` status, and is not discovered,
activated, or exported:

```powershell
rook skill stage --bundle evals\candidates\release-manifest\effective.toml
```

The command prints the canonical CandidateStore version directory to pass to
`rook eval run`.

Evaluate a CandidateStore version. Agents must be explicit. Codex additionally requires both external-call and cost acknowledgement flags:

```powershell
rook eval run `
  --skill-path .rook\skill-registry\example\candidates\1 `
  --suite evals\suites\codex-demo\suite.toml `
  --agents rook,codex `
  --model gpt-5.6-sol `
  --allow-external `
  --allow-costs
```

Bound one measurement explicitly with `--families content|routing`,
`--phase auto|fast|full`, `--repetitions`, and
`--fast-count-per-category`. `--measurement-only` still writes immutable
records, ScoreCards, and decisions into the report, but does not append
Registry history or change an active pointer. For content-only Full runs, the
scheduled Agent call count is exactly `cases x repetitions x 2`. A promoted
result prints `Gate passed, awaiting approval`; evaluation never activates a
Skill as a side effect.

If the network requires a local proxy, set it only for the current process and
append `--inherit-proxy` to `rook eval run`:

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:10808'
$env:HTTPS_PROXY = 'http://127.0.0.1:10808'
$env:ALL_PROXY = 'http://127.0.0.1:10808'
```

Inspect reports and Registry state, approve one exact decision, or review the immutable lifecycle:

```powershell
rook eval report <evaluation-id>
rook skill status <skill-name>
rook skill approve <skill-name> --agent rook --decision-id <decision-id> --suite <suite.toml> --approver <name> --reason <text>
rook skill approve <skill-name> --agent codex --decision-id <decision-id> --suite <suite.toml> --approver <name> --reason <text>
rook skill history <skill-name>
rook skill rollback <skill-name> --agent codex --to-version 1 --approver <name> --reason <text>
rook skill export <skill-name> --agent codex --output .\staged-export
```

Approval re-probes the current Agent and revalidates the model, Adapter,
Normalizer, Suite, Policy, and Candidate content fingerprints. Rook approval
changes only the project Registry; runtime discovery reads only the approved
Rook pointer. Codex approval installs an owned directory at
`.agents/skills/<skill-name>` in the current repository. Rook refuses to
overwrite an unmanaged directory and reports a Rook-managed directory as
`drifted` after manual changes. It never installs into a user's global Codex
directory.

Export is a review-oriented copy of an already approved, non-stale active
version. Rook refuses to export directly into the real `~/.codex` tree.
`/forge` and `/forge <skill-name>` provide a read-only TUI view of Candidates,
gates, approvals, deployed versions, ScoreCard metrics, report paths, drift,
and release history. All mutations remain explicit CLI operations.

## Trace-derived candidates

Automatic candidate generation is opt-in and remains outside the promotion path by default:

```toml
[evolution]
enabled = true
scope = "auto"
allow_global = true
max_skills_per_task = 2
```

For a verified completed task segment, Rook sends a redacted, bounded evidence summary to the active provider with tools disabled. The strict parser resolves model-produced `event_id:part_id` labels back to EvidenceRef values from that same segment. Unknown fields, invented references, unsafe content, or provider failures produce only a stable audit reason code.

Accepted output is stored centrally under `.rook/skill-registry/<name>/candidates/<version>` with `quarantined` status. It is not written to `.agents/skills`, discovered by the runtime, exported, or made active. Evaluate it explicitly with `rook eval run`; only the existing ScoreCard and automatic gate can make it eligible, and only a later human approval can deploy it.

## Optional live smoke

Live Codex smoke tests remain skipped unless external execution and costs are separately authorized:

```powershell
$env:ROOK_RUN_EXTERNAL_EVALS = '1'
$env:ROOK_ALLOW_MODEL_COSTS = '1'
$env:ROOK_CODEX_EVAL_MODEL = 'gpt-5.6-sol'
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo.py -k live
```

For the opt-in live smoke behind a proxy, also set the three proxy variables
above and `$env:ROOK_EVAL_INHERIT_PROXY = '1'`.

Do not set these variables in ordinary unit-test or CI jobs.

Rook does not inherit proxy variables by default. `--inherit-proxy` is an
explicit opt-in and passes only `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and
`NO_PROXY` variants through the existing Codex environment allowlist. Proxy
values are not written to process metadata or reports.

On native Windows, Rook also sets `windows.sandbox="unelevated"` explicitly
while retaining `--sandbox workspace-write` and `approval_policy="never"`.
This is required because EvalOps ignores user configuration and must not fall
back to a read-only or machine-specific Windows backend. Rook never uses the
dangerous no-sandbox flag for EvalOps.

Codex EvalOps also disables user plugins and memories. For the content-effect
pair, Rook sets `skills.include_instructions=false`: Baseline receives no
ambient Skill catalog, while Forced Skill reads the mounted Candidate through
the explicit relative path in its treatment prompt. The routing-effect pair
keeps Skill instructions enabled so natural discovery remains testable. This
prevents unrelated user Skills from confounding content attribution without
pretending that routed activation is observable on Codex.

Every Codex EvalOps invocation also passes
`web_search="disabled"` and
`sandbox_workspace_write.network_access=false`. Command networking remains
blocked by the workspace sandbox. A `web_search` event in a network-disabled
run is normalized as a fatal policy violation rather than accepted as task
evidence. JSONL decoding rejects duplicate JSON keys.

## Portfolio evidence suite

`evals/suites/release-manifest` contains 12 versioned cases: three each for
Direct, Transfer, Regression, and Adversarial behavior. Three manual bundles
under `evals/candidates/release-manifest` represent an effective procedure, a
neutral procedure, and an intentionally unsafe control.

Run the zero-cost control-plane proof with:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_portfolio.py
```

The Fake Agent control must promote the effective version, reject the neutral
version, and reject the unsafe version. These outcomes prove orchestration and
policy behavior only. They are not evidence of model quality or real success
uplift. See [Portfolio Evidence](PORTFOLIO_EVIDENCE.md) for the measurement
contract that must be completed before publishing resume metrics.

## RM-2 differential evidence protocol

`evals/suites/release-manifest-v2` is the resume-facing differential suite.
Direct and Transfer cases measure capability; Regression and Adversarial cases
measure preservation and safety. Its semantic Validator is not mounted into
the Agent workspace and is fingerprinted as suite evidence.

Stage the effective Candidate offline:

```powershell
rook skill stage --bundle evals\candidates\release-manifest-v2\effective.toml
```

After using the printed CandidateStore path, the first live stage has this
shape and schedules exactly 12 calls:

```powershell
rook eval run `
  --skill-path <printed-candidate-version-directory> `
  --suite evals\suites\release-manifest-v2\calibration.toml `
  --agents codex `
  --model gpt-5.6-sol `
  --families content `
  --phase full `
  --repetitions 1 `
  --measurement-only `
  --allow-external `
  --allow-costs `
  --inherit-proxy
```

An earlier authorized Calibration produced five complete comparable pairs:
Baseline success 20%, Forced Skill success 100% (+80 percentage points),
median latency -27.4%, and median Token use +17.2% among the three pairs with
complete Token observations. It was quarantined with
`excess_infrastructure_exclusions`, so it does not authorize deployment and is
not a Formal resume result.

Future Calibration, Pilot, and Formal stages require separate authorizations
for 12, 24, and 72 calls. Do not infer one stage's authorization from another.
Only the 72-call Formal immutable report may populate final resume success,
Token, and latency values; USD cost remains `not observed` unless the Adapter
receives a real cost field.

The repository-level Codex target and network controls follow the official
[Codex Skill documentation](https://learn.chatgpt.com/docs/build-skills) and
[Codex network and sandbox guidance](https://learn.chatgpt.com/docs/agent-approvals-security#network-access).

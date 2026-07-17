# Rook Codex-only EvalOps

Rook evaluates a stored Skill candidate with isolated Baseline, Forced Skill, and Routed Skill runs. The MVP supports the in-process Rook target and Codex CLI; Claude Code is not part of this release.

## Deterministic demo

The version-controlled demo suite contains Direct, Transfer, Regression, and Adversarial cases:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo.py
```

The default test path uses `FakeAgentAdapter`. It does not launch Codex, call a model API, or create model charges. The demo exercises candidate storage, paired A/B runs, ScoreCard construction, promotion history, reports, and rollback.

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
scheduled Agent call count is exactly `cases x repetitions x 2`.

If the network requires a local proxy, set it only for the current process and
append `--inherit-proxy` to `rook eval run`:

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:10808'
$env:HTTPS_PROXY = 'http://127.0.0.1:10808'
$env:ALL_PROXY = 'http://127.0.0.1:10808'
```

Inspect reports and registry state:

```powershell
rook eval report <evaluation-id>
rook skill status <skill-name>
rook skill rollback <skill-name> --agent codex --to-version 1
rook skill export <skill-name> --agent codex --output .\staged-export
```

Export requires a promoted, non-stale target decision. Rook refuses to export directly into the real `~/.codex` tree; the output is a reviewable staging directory.

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

Accepted output is stored centrally under `.rook/skill-registry/<name>/candidates/<version>` with `quarantined` status. It is not written to `.agents/skills`, discovered by the runtime, exported, or made active. Evaluate it explicitly with `rook eval run`; only the existing ScoreCard, decision, and Registry path can later make an evaluated version eligible for staged export.

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

Calibration, Pilot, and Formal require separate authorizations for 12, 24,
and 72 calls. Do not infer one stage's authorization from another. Only the
72-call Formal immutable report may populate resume success, Token, and
latency values; USD cost remains `not observed` unless the Adapter receives a
real cost field.

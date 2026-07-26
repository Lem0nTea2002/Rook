# Rook Forge: Codex-only Skill Governance

Rook Forge evaluates a stored Skill candidate with isolated Baseline, Forced Skill, and Routed Skill runs, applies an automatic gate, waits for immutable human approval, and then deploys independently to the in-process Rook runtime or the current repository's Codex Skill directory. The implementation package remains `evalops`; Claude Code is not part of this release.

```text
Candidate quarantine -> paired exam -> ScoreCard -> automatic gate
  -> human approval per target -> deploy -> stale/drift check -> rollback
```

`promoted` means **eligible for approval**, not active. Safety failures, secret leaks, new regressions, stale evidence, and content-hash mismatches cannot be overridden by an approver.

## Deterministic demo

Run the complete product lifecycle with the installed CLI:

```powershell
rook eval demo
```

The command creates a unique sandbox below `.rook/forge-demo`, uses the packaged Direct, Transfer, Regression, and Adversarial suite, and writes JSON/Markdown summaries plus immutable evidence. It uses `FakeAgentAdapter`: it does not probe or launch Codex, call a model API, access the network, or create model charges. The demo exercises Candidate storage, paired A/B runs, ScoreCard construction, automatic gate history, human approval, isolated Rook/Codex deployment, immutable release history, and rollback.

The focused regression test is still available:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo.py
```

See [Offline Demo](DEMO.md) for the artifact layout and expected checkpoints.

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
rook eval trends <skill-name> --agent rook|codex
rook skill status <skill-name>
rook skill approve <skill-name> --agent rook --decision-id <decision-id> --suite <suite.toml> --approver <name> --reason <text>
rook skill approve <skill-name> --agent codex --decision-id <decision-id> --suite <suite.toml> --approver <name> --reason <text>
rook skill history <skill-name>
rook skill rollback <skill-name> --agent codex --to-version 1 --approver <name> --reason <text>
rook skill export <skill-name> --agent codex --output .\staged-export
```

`rook eval trends` reads only bounded, redacted immutable ScoreCards. It
compares adjacent entries only when target and suite fingerprints match, and
shows gate reasons, success/latency/Token deltas, SLO breaches, fingerprint
boundaries, and approval/release/rollback counts. It never launches an Agent
or calls a model. Add `--json` for stable machine output.

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

Rook serializes the Codex `-C` workspace argument with forward slashes on
Windows while retaining the native `Path` as the outer process CWD. This keeps
sequences such as `\b` in a generated `baseline` directory from becoming
control characters at the inner sandbox boundary. Any Windows sandbox setup,
runner refresh, or `CreateProcessAsUserW` failure in stderr is normalized as a
fail-closed infrastructure error even when the outer process later exits zero.

For the same Windows subprocess, Rook sets
`sandbox_workspace_write.exclude_tmpdir_env_var=true`. This prevents Codex
from granting model-run tools a second writable root for `TEMP`/`TMPDIR`,
which the Windows restricted-token backend cannot enforce. Rook deliberately
does not redirect those variables beneath the workspace: Codex 0.144.x's
compatibility projection recognizes such a nested temp directory as writable
through the workspace itself and reintroduces it as a separate legacy root.
The ordinary OS temp variables remain available to the trusted Codex CLI
process, but are excluded from the workspace-write policy applied to tools.

Codex 0.144.x on native Windows can also reject its in-process `apply_patch`
filesystem write even when a shell write to the same isolated workspace is
allowed. Rook therefore gives both sides of a Windows A/B pair the same
execution constraint: file changes must use sandboxed shell commands rather
than `apply_patch`. Content-effect runs also work only from task inputs and an
explicitly named Candidate instead of searching for missing repository
guidance. Baseline must still finish with a best-effort result, so lack of the
Candidate is measured as task failure instead of an infrastructure timeout.

Codex can emit a transient JSONL reconnect error before recovering. Rook accepts
only the exact bounded `Reconnecting... n/m (...)` event when the process exits
successfully and exactly one `turn.completed` event follows. The diagnostic is
retained in the trace. Generic stream errors, missing terminal events, Windows
sandbox markers, and unsuccessful processes remain infrastructure failures.

Codex's built-in ChatGPT provider prefers WebSockets. On networks where that
transport is blocked, the CLI can consume its full reconnect budget before
falling back to HTTPS. Adapter v5 therefore defines a controlled provider for
the same `https://chatgpt.com/backend-api/codex` endpoint, keeps
`requires_openai_auth=true`, uses the Responses wire API, and sets
`supports_websockets=false`. Every EvalOps call starts with HTTP/SSE while still
using the existing ChatGPT login. These exact overrides are passed after
`--ignore-user-config`, so ambient provider configuration cannot change the
experiment transport.

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

Use the dedicated Pilot manifest for the 24-call stage:

```powershell
rook eval run `
  --skill-path <printed-candidate-version-directory> `
  --suite evals\suites\release-manifest-v2\pilot.toml `
  --agents codex `
  --model gpt-5.4-mini `
  --families content `
  --phase full `
  --repetitions 1 `
  --measurement-only `
  --allow-external `
  --allow-costs `
  --inherit-proxy
```

The 72-call Formal stage deliberately uses the stricter sealed `suite.toml`
holdout with three repetitions. Its case IDs and fixture hashes are disjoint
from Pilot, and the manifest locks the frozen Candidate content hash. A changed
Candidate is rejected before any Agent call. Never use `suite.toml` for a
24-call Pilot: its policy requires 18 capability pairs, which only the 72-call
Formal plan can supply. Suite v5 makes the repository-root output contract
explicit and gives each Agent arm 180 seconds; these protocol changes produce a
new suite fingerprint without changing the Candidate.

```powershell
rook eval run `
  --skill-path <printed-candidate-version-directory> `
  --suite evals\suites\release-manifest-v2\suite.toml `
  --agents codex `
  --model gpt-5.4-mini `
  --families content `
  --phase full `
  --repetitions 3 `
  --measurement-only `
  --allow-external `
  --allow-costs `
  --inherit-proxy
```

The first authorized Formal attempt was aborted after the protocol exposed
Windows CWD serialization, intermittent sandbox work-directory drift, an
ambiguous output-location contract, and recovered-stream classification bugs.
Eighteen calls were started across that attempt and its bounded diagnostics;
17 produced terminal process artifacts and one was force-cancelled before an
artifact. No immutable Formal result was produced, so none of those values are
resume evidence. See the redacted
[Formal readiness incident](evidence/rm2-formal-readiness-2026-07-20.json).
Suite v5 and Adapter v4 required a fresh 2-call smoke. That smoke completed both
authorized calls but quarantined the pair as `trace_incomplete`: each arm spent
four retries before WebSocket-to-HTTPS fallback and then hit the 180-second
boundary without `turn.completed`. It produced no Formal result. The redacted
record is [Adapter v4 smoke evidence](evidence/rm2-v4-smoke-2026-07-21.json).
The separately authorized Adapter v5 smoke then completed exactly two HTTP/SSE
calls. Both processes exited successfully with one `turn.completed` event,
zero reconnect or WebSocket-fallback events, 100% trace completeness, and zero
infrastructure exclusions. Baseline produced `wrong_result`; Forced Skill
passed. The decision is still `quarantined (insufficient_valid_pairs)` because
the smoke intentionally supplies only one pair to a policy with a higher sample
threshold. This is a passed transport/trace readiness gate, not a Formal result.
See [Adapter v5 smoke evidence](evidence/rm2-v5-smoke-2026-07-22.json). A new
72-call Formal execution was then separately authorized. Rook stopped it
fail-closed after `holdout-mobile` repetition 3 Forced Skill reached the
180-second boundary without `turn.completed`. Thirty-two calls had started, 31
process artifacts existed, and 40 calls had not started. The 31 process
artifacts contained zero reconnect, WebSocket fallback, top-level stream error,
Windows sandbox-failure, or Web Search markers, so this was an Agent constraint
timeout rather than the earlier transport failure. The strict 100% trace gate
was no longer attainable, so no partial effect metrics, immutable ScoreCard, or
promotion decision were produced. See the redacted
[Adapter v5 Formal attempt](evidence/rm2-formal-v5-attempt-2026-07-22.json).
Any remediation that changes Adapter, Normalizer, suite, or policy identity
requires a new readiness smoke and a new explicit Formal authorization.

The timeout trace contained ten completed shell commands, five failures, and no
terminal turn. The first two failures exposed an outer PowerShell profile
language-mode conflict, nested quoting damage, and a constrained-language method
restriction. The Agent then retried shell variants, checked an output that had
never been created, and spent late attempts probing launchers. It eventually
found that `py` worked, but too late to execute the requested transformation
inside the 180-second boundary. This was not `CommandNotFound`, authentication,
transport, Web Search, or sandbox startup failure.

Adapter v6 adds a shared bounded recovery contract to both Rook and Codex EvalOps:
after two consecutive restricted PowerShell failures, the Agent must stop trying
PowerShell variants and make one direct attempt with `cmd.exe /d /s /c`, a direct
executable such as `py`, or a dedicated non-shell tool. It must use that attempt
for the task rather than a capability probe. If it fails, the Agent reports
`ROOK_SHELL_FALLBACK_EXHAUSTED: <short reason>` and stops issuing shell commands.
Normalizer v2 records threshold, recovery, and exhaustion diagnostics without
including raw command output in normalized events. A timeout after the threshold
now reports `codex_restricted_shell_timeout`. The original trace replays into the
new threshold and recovery diagnostics, but remains incomplete and cannot become
Formal evidence. See the redacted
[remediation record](evidence/rm2-formal-v5-shell-remediation-2026-07-22.json).

A separately authorized Adapter v6 smoke then ran exactly one Baseline/Forced
pair. Both calls exited normally, produced one terminal turn, and emitted the
stable exhaustion marker in 84.641s and 70.906s, so the prior silent 180-second
retry failure did not recur. The evaluation readiness gate nevertheless failed:
Baseline hit `codex_windows_sandbox_error` after a model-supplied path encoded
`\b` as a backspace, and Forced Skill hit `codex_shell_fallback_exhausted` when
escaped newlines were passed literally to `py -c`. Both runs were excluded and
the gate was `quarantined (excess_infrastructure_exclusions)`. See the redacted
[Adapter v6 smoke record](evidence/rm2-v6-smoke-2026-07-22.json). No further
live calls or Formal continuation were authorized by that smoke.

Adapter v7 addresses both newly observed shapes without changing the Candidate
or sealed suite. Its EvalOps prompt prohibits tool-level working-directory
overrides and requires relative forward-slash paths. Shared Windows recovery
guidance prohibits multiline or escaped-newline `py -c` source, and the Adapter
now reports error 267 with an escaped `cwd` as
`codex_windows_tool_cwd_escape_error`. The live `Cannot create type` message is
also recognized as a restricted PowerShell failure. See the offline
[v7 follow-up](evidence/rm2-v6-smoke-remediation-2026-07-22.json).

A separately authorized Adapter v7 readiness smoke then completed exactly one
Baseline/Forced pair. Both processes exited successfully with a terminal turn,
100% trace completeness, and zero infrastructure exclusions, reconnect events,
shell-fallback markers, Web Search events, or Windows sandbox failures. Baseline
produced `wrong_result`; Forced Skill passed. The one-pair automatic decision was
correctly `quarantined (insufficient_valid_pairs)`, so the result is readiness
evidence rather than a Formal effect estimate. See the redacted
[Adapter v7 smoke record](evidence/rm2-v7-smoke-2026-07-22.json).

The separately authorized v7 Formal attempt was stopped fail-closed after 30
calls started. It retained 29 process artifacts and 28 evaluated-run records;
42 calls never started, and no experiment record, ScoreCard, promotion decision,
or report was written. Windows Event Log shows that the host entered System Idle
sleep during one 180-second subprocess and corrected the clock forward by
18,957,278 ms after resume, which closely matches the observed 18,983,156 ms run
duration. A sleeping host cannot execute the runner's deadline loop. Three other
runs exhausted the bounded shell fallback, so the strict Formal evidence gate
was already unattainable. The process tree was stopped, partial results were not
scored, and they must not be combined with a future run. See the redacted
[Adapter v7 Formal attempt](evidence/rm2-formal-v7-attempt-2026-07-22.json).

Adapter v8 holds
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` around each Windows
EvalOps subprocess, fails closed if the guard cannot be acquired, surfaces guard
restore failures as cleanup failures, and adds `timeout_deadline_overrun` when a
timeout exceeds its deadline by more than five seconds. The Codex Adapter maps
that diagnostic to `codex_timeout_deadline_overrun` as an infrastructure error.
This remediation is offline-verified only; see the redacted
[v8 host-sleep remediation](evidence/rm2-formal-v7-host-sleep-remediation-2026-07-22.json).
Because the Adapter identity changed, v8 required a new explicitly authorized
2-call readiness smoke. That smoke completed both arms with terminal traces,
100% trace completeness, zero infrastructure exclusions, and no deadline
overrun. Baseline produced `wrong_result`; Forced Skill passed. The one-pair
decision remains `quarantined (insufficient_valid_pairs)` by design and cannot
serve as a Formal effect estimate. See the redacted
[Adapter v8 smoke record](evidence/rm2-v8-smoke-2026-07-22.json). A fresh
72-call Formal was subsequently authorized. Rook stopped it fail-closed after
13 calls started: 12 produced complete terminal artifacts, one in-flight call
was stopped before artifact persistence, and 59 calls were not started. The
failing `holdout-application` Forced arm wrote `release.json`, then an auxiliary
source-normalization assertion failed and the Agent emitted
`ROOK_SHELL_FALLBACK_EXHAUSTED`. Adapter v8 correctly classified the run as an
Adapter error; the deterministic evaluator did not run, and the zero-exclusion
Formal contract was no longer attainable. No experiment record, ScoreCard,
promotion decision, or Formal metric was produced. See the redacted
[Adapter v8 Formal attempt](evidence/rm2-formal-v8-attempt-2026-07-22.json).
Partial data from this attempt must not be reused.

Adapter v9 separates the required fallback mutation from auxiliary verification.
`ROOK_SHELL_FALLBACK_EXHAUSTED` remains a fail-closed Adapter error only when
the required mutation did not complete. If the output was written and only an
auxiliary check is inconclusive, the Agent reports
`ROOK_POST_WRITE_VERIFICATION_INCONCLUSIVE`; Normalizer v3 preserves that audit
diagnostic while allowing the deterministic evaluator to decide the workspace
result. The system prompt is v15. This remediation has only offline evidence;
see [Adapter v9 post-write remediation](evidence/rm2-formal-v8-post-write-remediation-2026-07-22.json).
A new v9 two-call readiness smoke requires separate authorization. It uses the
single-case `post-write-smoke.toml` manifest so both arms exercise the exact
`holdout-application` boundary that stopped the v8 Formal run:

```powershell
rook eval run `
  --skill-path <printed-candidate-version-directory> `
  --suite evals\suites\release-manifest-v2\post-write-smoke.toml `
  --agents codex `
  --model gpt-5.4-mini `
  --families content `
  --phase full `
  --repetitions 1 `
  --measurement-only `
  --allow-external `
  --allow-costs `
  --inherit-proxy
```

This schedules exactly one Baseline/Forced pair. Passing readiness requires two
terminal traces, complete deterministic evaluations, and zero infrastructure
exclusions; the one pair remains ineligible as a Formal effect estimate.

The separately authorized v9 smoke completed exactly those two calls. Both
processes exited 0 with one terminal event, trace completeness was 100%, and
infrastructure exclusions, Web Search, reconnect, stream-error, and Windows
sandbox-failure counts were all zero. Baseline failed the hidden evaluator with
`source_modified`; Forced Skill passed. The automatic gate correctly remained
`quarantined (insufficient_valid_pairs)`, because readiness is not an effect
study. The redacted record is
[`rm2-v9-smoke-2026-07-24.json`](evidence/rm2-v9-smoke-2026-07-24.json).
The following Adapter v9 Formal stopped fail-closed after 39/72 calls when the
real PowerShell profile loaded inside the restricted Windows sandbox. Adapter
v10 attempted to disable login shells with an invalid nested Codex config key;
its readiness Baseline failed config parsing before provider initialization,
produced an empty JSONL file, and stopped before the Forced arm. Adapter v11
uses the Codex 0.144.6 top-level field `allow_login_shell=false`. Unlike the
invalidated `--version` probe, the offline verification fully loads
configuration via `features list`; the valid key exits 0 and the invalid nested
control exits 1. `rook eval doctor` now performs the same full immutable-config
validation before reporting Codex available. Evidence is recorded in
[`rm2-v10-smoke-attempt-2026-07-26.json`](evidence/rm2-v10-smoke-attempt-2026-07-26.json)
and
[`rm2-formal-v10-profile-isolation-remediation-2026-07-26.json`](evidence/rm2-formal-v10-profile-isolation-remediation-2026-07-26.json).

The separately authorized Adapter v11 run then completed both arms in
`profile-isolation-smoke.toml`. Both processes exited 0, each emitted one
terminal turn, trace completeness was 100%, and infrastructure exclusions plus
PowerShell profile, Web Search, reconnect, WebSocket, and Windows sandbox
failure markers were all zero. Baseline was `wrong_result`; Forced Skill
passed. The gate remained `quarantined (insufficient_valid_pairs)` because this
single pair is readiness evidence, not an effect estimate. See
[`rm2-v11-smoke-2026-07-26.json`](evidence/rm2-v11-smoke-2026-07-26.json).

Passing readiness did not authorize Formal. After a new, separate authorization,
Adapter v11 started from call 1 and completed all 72 processes and 36 pairs.
Baseline success was 25% and Forced Skill success was 100% (+75pp); median
latency improved 16.7%, median observed Token use improved 19.5%, and all 18
preservation pairs passed with zero new regressions. Trace completeness was
100%, all processes exited 0, and no infrastructure exclusion occurred. The
automatic gate returned `promoted (capability_success_uplift)`. Because the run
was measurement-only, it did not record human approval or deploy the Skill.
USD cost and Codex routing remain not observed. See
[`rm2-formal-v11-summary-2026-07-26.json`](evidence/rm2-formal-v11-summary-2026-07-26.json).

Calibration, Pilot, and Formal stages require separate authorizations for 12,
24, and 72 calls. Do not infer one stage's authorization from another. Only the
72-call Formal immutable report now populates final resume success, Token, and
latency values; USD cost remains `not observed` because the Adapter received no
cost field.

The repository-level Codex target and network controls follow the official
[Codex Skill documentation](https://learn.chatgpt.com/docs/build-skills) and
[Codex network and sandbox guidance](https://learn.chatgpt.com/docs/agent-approvals-security#network-access).

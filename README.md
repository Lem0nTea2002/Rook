<p align="center">
  <img src="assets/rook-logo.png" alt="Rook logo" width="156">
</p>

<h1 align="center">Rook</h1>

<p align="center">
  <strong>A local Python coding agent with Rook Forge Skill exams, approval, deployment, and rollback.</strong>
</p>

<p align="center">
  <a href="#quickstart"><img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white"></a>
  <a href="#tui"><img alt="Textual TUI" src="https://img.shields.io/badge/Textual-TUI-5B5BD6?style=flat-square"></a>
  <a href="#configuration"><img alt="OpenAI Compatible" src="https://img.shields.io/badge/OpenAI-Compatible-111827?style=flat-square"></a>
  <a href="#development"><img alt="pytest" src="https://img.shields.io/badge/pytest-tested-0A9EDC?style=flat-square&logo=pytest&logoColor=white"></a>
</p>

<p align="center">
  English
  · <a href="README.zh-CN.md">简体中文</a>
</p>

---

Rook is a real, runnable local Python coding agent. **Rook Forge** is its built-in Skill governance control plane: generated or manually authored Skills are examined with isolated paired experiments, held behind automatic safety gates, explicitly approved by a human, deployed independently to Rook or the current Codex repository, and rolled back through an immutable audit trail. The implementation package remains `rook_agent.evalops`.

If you want to understand how coding agents actually work, Rook keeps the moving parts visible instead of hiding them behind a black box.

- Evaluate whether a Skill improves an Agent before it can be approved or deployed.
- Learn the agent loop, tool calling, permissions, sessions, and context handling.
- Build on a small Python codebase with clear module boundaries.
- Use a local coding agent while still being able to inspect how it works.

![Rook planning, requesting permission, and completing a local task](docs/images/rook-demo.gif)

## Rook Forge

Rook treats a Skill as a versioned change that must pass an exam and a release review. Manual bundles and trace-derived output enter an inactive quarantine, then run through isolated Baseline/Forced and Baseline/Routed pairs. Deterministic evaluators produce ScoreCards; safety, regression, sample-size, and effect gates decide eligibility independently for each Agent target. A passing gate remains inactive until an explicit, auditable `rook skill approve` deploys it.

```mermaid
flowchart LR
    A["Task trace or manual bundle"] --> B["Quarantined Candidate"]
    B --> C["Isolated Baseline / Forced / Routed runs"]
    C --> D["Evaluator + ScoreCard"]
    D --> E{"Automatic gate"}
    E -->|pass| F["Eligible; awaiting approval"]
    E -->|fail| G["Rejected or quarantined"]
    F --> H{"Human approval per target"}
    H --> I["Deploy to Rook or repo Codex"]
    I --> J["Stale / drift detection"]
    J --> K["Atomic rollback"]
```

The version-controlled evidence protocol contains a 12-case development/Pilot
suite and a sealed, disjoint 12-case Formal holdout across service catalog,
application, package, deployment, operations, and ML-service repository shapes.
The Formal manifest locks the Candidate content hash and fails before any model
call if the Candidate changes. Fake Agent controls prove the control plane only.

After the native Windows sandbox fix, an authorized `gpt-5.4-mini` Pilot
completed 24/24 calls with 12 comparable pairs, zero infrastructure exclusions,
100% trace completeness, and zero new regressions. It observed Baseline 25% vs
Forced Skill 100% (+75pp), 22.7% lower median latency, and 12.9% lower median
Token use. The immutable run accidentally used the Formal sample threshold and
was quarantined; the dedicated Pilot policy now fixes that boundary. These are
Pilot measurements, not a final 72-call Formal resume result.

The first authorized Formal attempt was stopped after its evidence protocol
exposed Windows work-directory, task-contract, and recovered-stream
classification defects. Eighteen calls were started across the aborted attempt
and bounded diagnostics; no Formal metric was produced. The frozen Candidate
did not change. A 2-call v4 smoke then isolated two incomplete WebSocket turns.
The separately authorized Adapter v5 smoke completed exactly 2/2 HTTP/SSE calls
with one terminal event per arm, zero reconnect/fallback events, 100% trace
completeness, and zero infrastructure exclusions. Baseline produced the wrong
result while Forced Skill passed. Its automatic decision remains quarantined
only because a one-pair smoke is below the policy sample threshold; it is
readiness evidence, not a Formal metric. A 72-call Formal rerun still requires
separate authorization. That authorization was later granted, but the run was
stopped fail-closed after a Forced-Skill arm timed out at 180 seconds without a
terminal trace. By then 32 calls had started, 31 process artifacts existed, and
40 calls had not started. HTTP-only transport remained clean: reconnect,
fallback, top-level stream error, sandbox-failure, and Web Search counts were
all zero. No immutable ScoreCard or Formal resume metric was produced.
The failed trace was then replayed offline: after two restricted PowerShell
failures the Agent still retried shell variants and probes, finding a working
launcher too late. Adapter v6 now defines a two-failure prompt boundary, one
direct fallback attempt, explicit exhaustion feedback, and Normalizer v2 audit
codes. A separately authorized two-call v6 smoke then reached a terminal turn
and emitted the stable exhaustion marker in both arms instead of repeating the
180-second silent timeout. It still failed readiness: a model-supplied Windows
path encoded `\b` as a backspace in Baseline, while the Forced arm's direct
`py -c` fallback passed escaped newlines literally. Both runs were excluded, so
no Skill-effect or Formal metric was produced.
Adapter v7 now forbids tool-level `cwd` overrides, requires forward-slash
relative paths, constrains direct `py -c` recovery to one physical line, and
classifies escaped-cwd error 267 separately. A separately authorized v7
readiness smoke then completed exactly 2/2 calls with terminal traces, 100%
trace completeness, and zero infrastructure exclusions; Baseline was wrong and
Forced Skill passed. The following Formal run was stopped fail-closed after 30
calls started (29 process artifacts, 28 evaluated-run records, 42 not started).
One 180-second subprocess crossed a host idle-sleep interval and was terminated
after resume, while three other runs exhausted the bounded shell fallback. No
experiment record, ScoreCard, promotion decision, or Formal resume metric was
produced, and partial results will not be reused. Adapter v8 now inhibits
Windows system-idle sleep while EvalOps subprocesses run and classifies a
deadline overrun as infrastructure failure. A separately authorized v8 smoke
then completed exactly 2/2 calls with terminal traces, 100% trace completeness,
and zero infrastructure exclusions or timeout-overrun markers; Baseline was
wrong and Forced Skill passed. Its one pair is readiness evidence only. A fresh
72-call Formal was then authorized but stopped fail-closed after 13 calls
started: 12 produced complete terminal artifacts, one in-flight call was
stopped, and 59 were not started. A Forced arm emitted
`ROOK_SHELL_FALLBACK_EXHAUSTED` after writing the target and failing an auxiliary
verification assertion, so the zero-exclusion Formal contract was no longer
attainable. No ScoreCard or Formal resume metric was produced.

Run the complete zero-cost lifecycle from Candidate creation through dual-target rollback with one command:

```sh
rook eval demo
```

The command uses deterministic Fake Agents only and writes its isolated Registry, reports, Rook deployment, and repository-level Codex deployment below `.rook/forge-demo/run-*`. It never probes or launches Codex and makes no model or network call.

- [EvalOps usage](docs/EVALOPS.md)
- [Offline demo walkthrough](docs/DEMO.md)
- [Portfolio evidence and claim boundary](docs/PORTFOLIO_EVIDENCE.md)
- [Dogfooding and incident ledger](docs/DOGFOODING.md)
- [Redacted Pilot evidence](docs/evidence/rm2-pilot-summary.json)
- [Formal readiness incident](docs/evidence/rm2-formal-readiness-2026-07-20.json)
- [Failed Adapter v4 smoke](docs/evidence/rm2-v4-smoke-2026-07-21.json)
- [Passed Adapter v5 HTTP-only smoke](docs/evidence/rm2-v5-smoke-2026-07-22.json)
- [Aborted Adapter v5 Formal attempt](docs/evidence/rm2-formal-v5-attempt-2026-07-22.json)
- [Adapter v6 bounded-recovery smoke](docs/evidence/rm2-v6-smoke-2026-07-22.json)
- [Adapter v7 offline follow-up](docs/evidence/rm2-v6-smoke-remediation-2026-07-22.json)
- [Adapter v6 restricted-shell remediation](docs/evidence/rm2-formal-v5-shell-remediation-2026-07-22.json)
- [Passed Adapter v7 readiness smoke](docs/evidence/rm2-v7-smoke-2026-07-22.json)
- [Aborted Adapter v7 Formal attempt](docs/evidence/rm2-formal-v7-attempt-2026-07-22.json)
- [Adapter v8 host-sleep remediation](docs/evidence/rm2-formal-v7-host-sleep-remediation-2026-07-22.json)
- [Passed Adapter v8 readiness smoke](docs/evidence/rm2-v8-smoke-2026-07-22.json)
- [Aborted Adapter v8 Formal attempt](docs/evidence/rm2-formal-v8-attempt-2026-07-22.json)

## Why Rook

Most coding-agent demos show the surface: a prompt goes in, code changes come out. Rook focuses on the machinery in between.

Compared with larger projects like OpenCode, Rook is intentionally smaller in scope.

| Dimension | Rook | Larger projects like OpenCode |
| --- | --- | --- |
| Primary goal | Make agent internals readable and teachable | Deliver a broader production-style coding-agent platform |
| Codebase shape | Roughly 32k lines of Python runtime code in this repo | Roughly 575k lines of TS/JS across a much larger multi-surface codebase |
| Engineering tradeoff | Drops some extra platform surface area to stay inspectable | Accepts more complexity to support a broader product surface |
| Best fit | Learning, modification, interview prep, portfolio projects, and local experimentation | Users who want a larger, more full-surface coding-agent environment |

The goal is not to out-feature a bigger coding agent. The goal is to keep the system real enough to use, but small enough that you can still read it end to end and understand why each subsystem exists.

That also makes Rook a practical repo to study deeply, adapt for your own workflow, and turn into a resume-worthy or portfolio-friendly project after you have extended it.

Compared with more tutorial-first or lightweight learning repos, Rook also tries to stay closer to a small but testable engineering system.

| Dimension | Rook | Many learning-oriented agent repos |
| --- | --- | --- |
| Learning value | Readable subsystem boundaries and explicit docs | Often optimized for a single tutorial path or demo flow |
| Practical surface | Real TUI, tools, permissions, sessions, provider adapters | Often focused on a narrower loop or a simpler proof of concept |
| Verification | 120+ test files, cross-platform offline CI, and multiple benchmark entry points | Often lighter on testing and benchmark integration |
| Extension path | Easier to adapt into a portfolio or resume project | Often better for following along than for long-term extension |

In this repo, the learning goal is important, but it is paired with enough runtime structure, tests, and benchmark hooks to make the project useful after the first read-through.

It is built for people who want to:

- study how a coding agent is assembled
- modify or extend a local Python implementation
- understand the architecture well enough to explain it in an interview

Detailed subsystem design lives in the docs, not in this README.

## Quickstart

Install the tagged GitHub release with `pipx`:

```sh
pipx install "git+https://github.com/ZHUMUJUN/Rook.git@v0.2.1"
```

Or install from a local clone:

```sh
pipx install .
```

Start the TUI:

```sh
rook
```

Run one message without opening the TUI:

```sh
rook --message "Summarize this repository in one paragraph"
```

Use line-oriented interactive mode:

```sh
rook --interactive
```

Try Rook Forge without configuring a provider or spending model tokens:

```sh
rook eval demo
```

## What You Get

- Local Python coding agent
- Textual TUI that exposes agent activity instead of hiding it
- Tool calling with permission checks before risky actions
- Session persistence, resume flow, and context compaction
- Skills, provider adapters, and clean modules for study and modification
- Rook Forge Skill quarantine, isolated A/B exams, ScoreCards, human approval, target-specific deployment, and rollback

## Configuration

Create a starter config:

```sh
rook config init
rook config path
rook config show
```

Keep secrets in environment variables:

```sh
export ROOK_API_KEY="your-api-key"
```

Default config locations:

```text
global:  ~/.config/rook/config.toml
project: ./rook.toml
```

Provider support is centered on the OpenAI Chat Completions-compatible path. The OpenAI-compatible 流式 adapter is the mainline streaming implementation and normalizes provider errors such as PROMPT_TOO_LONG. The Anthropic provider is still 实验性 and does not yet expose Anthropic 原生 thinking/cache/streaming. Rook does not use the OpenAI Responses API yet, so native reasoning and 多模态 support are future provider work rather than current runtime behavior.

## TUI

Rook's TUI is designed to expose the agent loop instead of hiding it. You can see session state, streamed assistant output, tool calls, tool results, and permission prompts in one place.

Ready state:

![Rook ready state](docs/images/rook-ready.png)

Conversation flow:

![Rook conversation flow](docs/images/tui-empty.png)

## Documentation

- [Technical Docs Index](docs/README.md)
- [Chinese Docs Index](docs/README.zh-CN.md)
- [Codebase Reading Guide](docs/CODEBASE_READING_GUIDE.md)
- [Codex-only Skill EvalOps](docs/EVALOPS.md)
- [Offline Rook Forge Demo](docs/DEMO.md)
- [Portfolio Evidence](docs/PORTFOLIO_EVIDENCE.md)

## Development

Install dev dependencies:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Run all tests:

```sh
.venv/bin/python -m pytest
```

Run a focused test file:

```sh
.venv/bin/python -m pytest tests/test_app_tui.py -q
```

## Philosophy

Rook was built to answer a question most coding agents do not address:

> What actually happens inside when an agent streams, calls tools, asks for
> permission, compacts context, and resumes a session?

It is a real runnable agent, but it is also a readable Python project you can learn from one subsystem at a time.

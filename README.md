<p align="center">
  <img src="assets/rookie-mascot.png" alt="Rookie mascot" width="160">
</p>

<h1 align="center">Rook</h1>

<p align="center">
  <strong>A local Python coding agent with Skill exams, approval, deployment, and rollback.</strong>
</p>

<p align="center">
  <a href="https://github.com/Lem0nTea2002/Rook/releases/tag/v0.5.0"><img alt="Release v0.5.0" src="https://img.shields.io/badge/release-v0.5.0-38CFE0?style=flat-square"></a>
  <a href="https://github.com/Lem0nTea2002/Rook/actions/workflows/offline-tests.yml"><img alt="Offline CI" src="https://img.shields.io/badge/CI-Windows%20%7C%20Linux-61D095?style=flat-square"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <a href="README.zh-CN.md">简体中文</a>
</p>

Rook reads and edits code in a local workspace, calls tools, runs tests, and keeps
recoverable sessions. Its built-in **Rook Forge** decides whether a Skill is safe and
useful enough to ship.

> Rook completes coding tasks. Rook Forge tests, approves, deploys, and rolls back Skills.

## Demo

![Rook product demo: Rookie startup, coding workflow, and Skill release gates](docs/images/rook-demo.gif)

[Run the three-minute interview demo](docs/THREE_MINUTE_DEMO.zh-CN.md): Coding
Task → Tool Call → Skill exam → gate → approval → deploy → drift → rollback.

## Quickstart

```bash
pipx install --backend pip "git+https://github.com/Lem0nTea2002/Rook.git@v0.5.0"
rook
```

On the first interactive launch, Rook guides you through Provider, model, Base
URL, and API-key setup. The wizard does not send a model request.

Run one task without opening the TUI:

```bash
rook --message "Inspect this project and fix the failing test"
```

Try the complete Rook Forge lifecycle without a model or API cost:

```bash
rook eval demo
```

## Features

| Capability | What it provides |
| --- | --- |
| Coding agent | Read, search, edit, run commands, and verify changes |
| Coding workbench | Selectable output, Slash Palette, `@` files, controlled shell, diff, and transcript |
| Visible agent loop | Stream model output, tool calls, results, permission requests, and todos |
| Permissions and sessions | Use explicit ASK, AUTO, or local-only FULL access; persist, resume, and compact sessions |
| Confirmed learning | Detect verified failure recovery for free, then let the user save project memory or quarantine a Skill Candidate |
| Mobile channels | Paired Feishu/WeChat DMs submit tasks and approve one sensitive action |
| Skill exams | Run isolated Baseline, Forced, and Routed paired experiments |
| Skill releases | Require human approval, deploy to Rook/Codex independently, and roll back |
| External review | Submit read-only workspace/range/commit reviews to Review Agent and preserve Finding provenance |

## Review Agent code review

Rook can call an independently running [Review Agent](https://github.com/Lem0nTea2002/Review-Agent) service (the compatible API and configuration fields retain the `evoagent` name). Configure only the service URL,
project alias, and default reviewers in `rook.toml`; store the administrator password in
the operating-system keyring:

```toml
[review]
url = "http://127.0.0.1:8080"
project = "rook"
reviewers = ["local", "ocr"]
```

```powershell
rook review login --username admin
rook review doctor
rook review run --target workspace
rook review report <task-id>
```

Inside the TUI, use `/review workspace` and `/review-report <task-id>`. Network access
still follows Rook's ASK/AUTO/FULL policy. Reports retain `local-rules` and
`open-code-review` sources. Selecting `/review-report <task-id> --fix <n>` creates a
normal Rook coding task; edits, Shell commands, and tests continue through Rook permissions.

## Control local Rook from Feishu or WeChat

Rook v0.5.0 can run a local gateway. Your phone sends tasks and approves a
single sensitive action; project files, model credentials, the Agent Loop,
Skills, and tool execution remain on your computer.

```powershell
pipx inject rook-agent "lark-oapi>=1.7,<2" "qrcode>=8,<9"
rook channel project add rook --path "D:\absolute\path\to\Rook"
rook channel setup feishu
rook channel login weixin
rook channel pair create --channel feishu --project rook
rook channel serve --channels feishu,weixin
```

`channel setup feishu` uses Feishu's official QR registration flow and stores
the returned secret directly in the operating-system credential manager. To
reuse an existing app, pass `--app-id cli_xxx`; its secret is read from a
hidden local prompt and must never be pasted into chat or a command argument.

Only a paired user's private text messages and explicitly whitelisted projects
are accepted. Feishu uses approval cards and WeChat uses six-digit codes. Mobile
approval is allow-once or deny only and expires as denial after five minutes.
The TUI and mobile gateway share the same permission manager, session store,
and project execution lock.

![Rook 手机渠道演示](docs/images/rook-mobile-demo.gif)

[Full mobile-channel setup and security model](docs/MOBILE_CHANNELS.md)

[Redacted WeChat iLink + DeepSeek live acceptance evidence](docs/evidence/rook-weixin-live-acceptance-2026-07-29.json)

## How Rook Forge works

```text
Candidate quarantine
        ↓
Baseline / Forced / Routed exams
        ↓
Evaluator + ScoreCard
        ↓
Automatic gate → Human approval → Per-target deployment
        ↓
stale / drift detection → Atomic rollback
```

The automatic gate only decides release eligibility; it never activates a Skill.
Human approval cannot bypass a safety failure, secret leak, new regression, stale
evidence, or content-hash mismatch.

## Permissions and confirmed learning

Rook exposes three permission modes:

| Mode | Boundary |
| --- | --- |
| `ASK` | Ask for every permission-bearing file, Shell, network, environment, and Git action |
| `AUTO` | Allow ordinary in-project file access and an explicit safe command list; ask for network, deletion, project escape, secrets, and risky Git |
| `FULL` | Local TUI/CLI only; bypass confirmation after an explicit session risk acknowledgement while retaining redacted audit events |

`Shift+Tab` cycles only `ASK` and `AUTO`. Enter `FULL` through `/permissions`;
mobile channels, EvalOps, and Candidate sandboxes cannot inherit it.

After a turn, a deterministic detector looks only for a real failure followed by
a corrected action and successful verification. Detection makes zero model calls.
`/learn last` explicitly asks the current Provider to draft a structured lesson;
the user can then save it under `.rook/memory/`, dismiss it, mark a runtime defect,
or send cross-project procedures to Rook Forge as a `quarantined` Candidate.
Unconfirmed or stale memory is never loaded, and no Candidate activates itself.
Tool-schema and protocol errors are handled by runtime validation and regression
tests; they cannot be saved as memory or routed around the bug as a Skill.

## Project memory effectiveness benchmark

The product loop is `verified recovery → user review → active project memory →
paired A/B on unseen tasks`. Baseline and Memory arms start from the same commit,
workspace hash, model, tool schema, container, and request budget. Stale, revoked,
and unconfirmed records remain present as negative controls; loading any of them
invalidates the evidence.

Current evidence stays deliberately conservative:

| Stage | Result |
| --- | --- |
| M0 offline readiness | Runtime, validator redaction, paired orchestration, ScoreCard, and stable JSON/Markdown/SVG reporting verified with zero Provider calls |
| Latest Pylint/Xarray targeted Pilot | Baseline 0% / Memory 0%; Xarray Memory produced a non-empty patch but regressed existing behavior; no Validator success |
| 20-pair Formal | Pending; no memory-effect resume metric is claimed |

Verify the public 20-pair catalog without a model call:

```powershell
rook benchmark memory verify --catalog benchmark/memory/v1/catalog.json
```

The next separately authorized M1 run is fixed to two tasks. The private sealed
manifest and local repository mirrors stay outside the public repository:

```powershell
rook --project . benchmark memory run --phase pilot `
  --validators <sealed-manifest.json> `
  --source "https://github.com/pylint-dev/pylint=D:\bench\pylint" `
  --source "https://github.com/pydata/xarray=D:\bench\xarray" `
  --task pylint-dev__pylint-7114 `
  --task pydata__xarray-3364 `
  --root D:\RMP-M1 --allow-external --allow-costs
```

See the [M0 readiness receipt](docs/benchmarks/MEMORY_M0_READINESS_2026-08-03.zh-CN.md),
[frozen A/B design](docs/benchmarks/MEMORY_AB_FREEZE_V1.zh-CN.md), and
[failed Pilot timeline](docs/benchmarks/MEMORY_PILOT_V1_2026-08-01.zh-CN.md).
Pilot data diagnoses the execution path; only a valid 20-pair Formal can become
a resume result. This Pilot is not a resume metric.

## TUI · Coding workbench

Typing `/` opens a searchable command palette immediately. Filter by command,
category, or description; use arrows to navigate, Tab to complete, and Enter to
run. Commands such as `/model`, `/resume`, `/use`, and `/mode` provide second-level
argument completion.

![Rook Coding Workbench with Slash Command palette and tool cards](docs/images/rook-tui-workbench.png)

| Input / shortcut | Purpose |
| --- | --- |
| `/status`, `/usage`, `/diff` | Inspect project state, observed usage, and a separate diff viewer |
| `/copy last\|code\|selection\|transcript` | Copy a reply, code block, selection, or the full session |
| `@src/app.py` | Reference a bounded project file snapshot with escape checks |
| `!git status` / `!` | Run once / toggle controlled shell through permissions, audit, and cancellation |
| `Ctrl+Shift+C` | Copy the selection, falling back to Rook's last reply |
| `Ctrl+R` | Search project-scoped prompt history |
| `Ctrl+X Ctrl+E` | Edit the prompt with `$VISUAL` or `$EDITOR` |
| `Shift+Tab` | Cycle `ASK` and `AUTO`; `FULL` requires `/permissions` |
| `Enter` / `Alt+Enter` while running | Steer the current turn / queue the next task |
| `↑` / `↓` then `Enter` on approval | Select and confirm `deny`, `allow once`, or scoped persistent access |

Output, Markdown, code blocks, and tool cards are selectable. Long tool output
expands on click. The full transcript stays in state while only the latest 200
entries are mounted, keeping long sessions responsive. Rookie still appears at
startup and steps aside after the first message.

![Rook startup screen with Rookie](docs/images/rook-tui-welcome.png)

Both screenshots use Rook's real Textual components and deterministic local data.
Rendering does not call a model.

## Verified results

| Validation | Result |
| --- | --- |
| `gpt-5.4-mini` Formal | 72/72 calls and 36 comparable pairs; Baseline 25% → Forced 94.4% (+69.4pp) |
| Efficiency and safety | Median latency -5.8%, median tokens -15.2%, 0 new regressions, 100% trace completeness |
| Cross-platform CI | 2,000+ offline tests on Ubuntu/Windows and Python 3.11/3.12; exact counts stay in CI |
| Release lifecycle | Real gate, human approval, dual-target deployment, drift detection, and atomic rollback |

The Formal used a sealed holdout. Dollar cost and Codex routing activation were not
observed, so Rook does not estimate them. Fake-Agent demos validate the control plane,
not model effectiveness.

Evidence:

- [Portfolio evidence contract](docs/PORTFOLIO_EVIDENCE.zh-CN.md)
- [Formal result](docs/PORTFOLIO_EVIDENCE.md#completed-candidate-v5--adapter-v12-formal)
- [Real release lifecycle](docs/evidence/rm2-v5-formal-release-2026-07-27.json)
- [Full-repository and scale execution](docs/FULL_REPO_AND_SCALE.md)

## Configuration

```bash
rook config setup
rook config init
rook config path
rook config show
```

`rook config setup` supports OpenAI, DeepSeek, Qwen, Moonshot, Zhipu,
OpenRouter, Anthropic, Ollama, and custom OpenAI-compatible endpoints. API keys
use hidden input and are stored in the operating-system credential manager,
never in `config.toml`.

Environment variables override stored credentials. For example:

```bash
export OPENAI_API_KEY="your-openai-api-key"
export OPENAI_MODEL="gpt-4.1-mini"
```

Custom OpenAI-compatible Providers default to `ROOK_API_KEY`,
`ROOK_BASE_URL`, and `ROOK_MODEL`. ChatGPT/Codex subscription authentication
is separate from OpenAI API authentication and is not reused by Rook.

Configuration files:

```text
global:  ~/.config/rook/config.toml
project: ./rook.toml
```

Project commands, UI, and keybindings can be configured in `rook.toml`:

```toml
[commands.review]
description = "Review the current changes"
argument_hint = "[path]"
prompt = "Review this scope for security and regressions: $ARGUMENTS"

[ui]
language = "zh-CN"
theme = "rook" # or high-contrast

[keybindings]
search_history = "ctrl+r"
open_model_picker = "alt+p"
```

Project commands override global custom commands but cannot replace built-ins.
Templates support `$ARGUMENTS` and `@file`; embedded shell execution is rejected.
Invalid configuration is reported by `/doctor` without preventing startup.

The main provider path is OpenAI Chat Completions-compatible and implements
OpenAI-compatible streaming, including normalized errors such as
`PROMPT_TOO_LONG`. Rook does not use the OpenAI Responses API yet, so native
reasoning and multimodal support remain future work. The Anthropic provider is
experimental and does not expose native thinking/cache/streaming.

## Project layout

```text
rook_agent/
├── agent/          # Model → Tool → Model loop
├── tools/          # File, search, edit, and command tools
├── permissions/    # Policy checks and human confirmation
├── context/        # Sessions, context projection, and compaction
├── providers/      # Model providers
├── evolution/      # Recovery detection, confirmed project memory, and Candidate routing
└── evalops/        # Rook Forge exam and release control plane
```

## Documentation

- [Documentation index](docs/README.md)
- [Codebase reading guide](docs/CODEBASE_READING_GUIDE.zh-CN.md)
- [Rook Forge offline demo](docs/DEMO.md)
- [Issue-to-PR demo](docs/ISSUE_TO_PR_DEMO.md)
- [Engineering article: treating Skills as software releases](docs/articles/ROOK_FORGE_FROM_SKILL_TO_RELEASE.zh-CN.md)

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Rook aims to be a real, inspectable coding agent with clear boundaries. Rook Forge adds
evidence, auditability, and rollback to the Skill release process.

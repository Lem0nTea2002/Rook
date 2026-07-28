<p align="center">
  <img src="assets/rookie-mascot.png" alt="Rookie mascot" width="160">
</p>

<h1 align="center">Rook</h1>

<p align="center">
  <strong>A local Python coding agent with Skill exams, approval, deployment, and rollback.</strong>
</p>

<p align="center">
  <a href="https://github.com/Lem0nTea2002/Rook/releases/tag/v0.2.6"><img alt="Release v0.2.6" src="https://img.shields.io/badge/release-v0.2.6-56A8FF?style=flat-square"></a>
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

## Quickstart

```bash
pipx install "git+https://github.com/Lem0nTea2002/Rook.git@v0.2.6"
rook
```

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
| Visible agent loop | Stream model output, tool calls, results, and todos in the TUI |
| Permissions and sessions | Confirm risky actions; persist, resume, and compact sessions |
| Skill exams | Run isolated Baseline, Forced, and Routed paired experiments |
| Skill releases | Require human approval, deploy to Rook/Codex independently, and roll back |

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

## TUI

Rookie appears whenever the TUI starts, then steps aside after the first message.

![Rook startup screen with Rookie](docs/images/rook-tui-welcome.png)

The mascot uses terminal-native color blocks, so it works without Kitty/Sixel image
support. The screenshot is rendered offline with Rook's real Textual components.

## Verified results

| Validation | Result |
| --- | --- |
| `gpt-5.4-mini` Formal | 72/72 calls and 36 comparable pairs; Baseline 25% → Forced 94.4% (+69.4pp) |
| Efficiency and safety | Median latency -5.8%, median tokens -15.2%, 0 new regressions, 100% trace completeness |
| Cross-platform CI | Ubuntu 1844 passed / 8 skipped; Windows 1845 passed / 7 skipped |
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
rook config init
rook config path
rook config show
```

Pass secrets through environment variables:

```bash
export ROOK_API_KEY="your-api-key"
```

Configuration files:

```text
global:  ~/.config/rook/config.toml
project: ./rook.toml
```

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

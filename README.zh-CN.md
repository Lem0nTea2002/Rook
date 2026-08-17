<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="assets/readme/hero-mobile.svg">
    <img src="assets/readme/hero.svg" alt="Rook 证据门禁式 Coding Agent：本地运行时、Rook Forge 发布流程与 Candidate v5 sealed holdout 结果" width="1200">
  </picture>
</p>

<h1 align="center">Rook</h1>

<p align="center">
  <strong>本地 Python Coding Agent，内置以证据为门禁的 Skill 发布控制面。</strong>
</p>

<p align="center">
  <a href="https://github.com/Lem0nTea2002/Rook/releases/tag/v0.5.0"><img alt="Release v0.5.0" src="https://img.shields.io/badge/release-v0.5.0-38CFE0?style=flat-square"></a>
  <a href="https://github.com/Lem0nTea2002/Rook/actions/workflows/offline-tests.yml"><img alt="Offline CI" src="https://github.com/Lem0nTea2002/Rook/actions/workflows/offline-tests.yml/badge.svg?branch=main"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <a href="README.md">English</a>
</p>

Rook 能在本地工作区读取和修改代码、调用工具、运行测试，并保存可恢复的会话。
内置的 **Rook Forge** 把 Skill 作为软件发布：隔离 Candidate、运行配对考试、执行
自动门禁、等待人工审批、按目标部署、检测漂移并原子回滚。

> Rook 负责完成 Coding Task；Rook Forge 负责判断一个 Skill 是否具备充分的发布证据。

## 已验证证据

下列 sealed holdout 使用 `gpt-5.4-mini` 评测 Candidate v5 Skill
`release-manifest-v2-normalizer`。

| 测量项 | 已观测结果 |
| --- | --- |
| `gpt-5.4-mini` sealed holdout | 72/72 次调用完成，形成 36 个可比配对 |
| Candidate v5 Skill 任务成功率 | Baseline 25.0% → Forced 94.4%（+69.4pp） |
| 效率 | 中位时延 -5.8%，完整观测 Token 中位数 -15.2% |
| 安全与审计 | 新增回归 0，轨迹完整度 100% |
| 离线 CI | Ubuntu/Windows、Python 3.11/3.12 执行 2,000+ 测试；精确数量以 CI 为准 |

Formal 使用 **sealed holdout**。美元成本与 Codex 路由激活状态均未观测，Rook
对这两项不作推断。Fake Agent 演示用于验证控制面与用户流程，不作为模型效果
测量。当前项目记忆 Pilot 为 Baseline 0% / Memory 0%，20-pair Formal 仍待执行。

证据链：

- [Formal 结果与表述边界](docs/PORTFOLIO_EVIDENCE.zh-CN.md#已完成的-candidate-v5--adapter-v12-formal)
- [真实发布生命周期回执](docs/evidence/rm2-v5-formal-release-2026-07-27.json)
- [完整仓库任务与规模执行](docs/FULL_REPO_AND_SCALE.md)
- [简历证据合同](docs/PORTFOLIO_EVIDENCE.zh-CN.md)

## 查看产品闭环

![Rook 动态演示：Rookie 启动页、Coding 工作流与 Skill 上线门禁](docs/images/rook-demo.gif)

GIF 使用 Rook 的真实 Textual 组件和确定性 Fake Agent，在零 Provider 调用、零 API
成本下展示完整流程。[三分钟面试演示](docs/THREE_MINUTE_DEMO.zh-CN.md)覆盖
Coding Task → Tool Call → Skill 考试 → Gate → 人工审批 → 部署 → drift → rollback。

## 工作机制

| Agent 运行时 | Skill 发布控制面 |
| --- | --- |
| Model → 权限化 Tool Call → Result → Verification | Candidate → Baseline/Forced/Routed 考试 → Evaluator + ScoreCard |
| 流式输出、可见工具卡、待办与错误 | 自动门禁 → 人工审批 → 按目标部署 |
| 会话持久化、上下文投影与压缩 | stale/drift 检测 → 内容哈希校验 → 原子回滚 |

自动门禁只决定发布资格，不会激活 Skill。人工审批不能绕过安全失败、秘密泄漏、
新增回归、stale 证据或内容哈希不一致。Rook 与 Codex 是两个独立部署目标。

## 快速开始

安装固定版本并打开 TUI：

```bash
pipx install --backend pip "git+https://github.com/Lem0nTea2002/Rook.git@v0.5.0"
rook
```

首次启动向导会配置 Provider、模型、Base URL 和 API Key，配置阶段不会发送模型请求。
直接运行一条任务：

```bash
rook --message "读取这个项目，修复失败的测试"
```

使用确定性 Fixture、零模型成本体验完整 Rook Forge 生命周期：

```bash
rook eval demo
```

## Rook 提供什么

| 能力 | 说明 |
| --- | --- |
| Coding Agent | 读取、搜索、修改代码，运行命令和测试 |
| Coding Workbench | 可复制输出、Slash Palette、`@` 文件、受控 Shell、Diff 和 Transcript |
| 可见 Agent Loop | 展示流式输出、工具调用、结果、权限请求和待办 |
| 权限与会话 | 明确的 ASK、AUTO 或仅限本地的 FULL；会话可持久化、恢复和压缩 |
| 确认式学习 | 零模型调用检测已验证恢复，再由用户保存项目记忆或隔离 Skill Candidate |
| Rook Forge | 隔离配对考试、ScoreCard、发布门禁、人工审批、双目标部署与回滚 |
| 手机渠道 | 已配对的飞书/微信私聊可提交任务，并审批一次敏感动作 |
| 外部审阅 | 向 Review Agent 提交只读 workspace/range/commit 审阅，并保留 Finding 来源 |

## 权限与可恢复会话

| 模式 | 边界 |
| --- | --- |
| `ASK` | 所有受权限管理的文件、Shell、网络、环境变量和 Git 动作都询问 |
| `AUTO` | 项目内普通文件访问和明确安全命令自动执行；网络、删除、越界、秘密和危险 Git 仍询问 |
| `FULL` | 仅限本地 TUI/CLI；会话级风险确认后跳过弹窗，同时继续写入脱敏审计事件 |

`Shift+Tab` 在 `ASK` 与 `AUTO` 间切换；`FULL` 必须通过 `/permissions` 显式进入。
手机渠道、EvalOps 和 Candidate 沙箱不能继承 `FULL`。

每个 Turn 结束后，确定性检测器会寻找“真实失败 → 修正动作 → 验证通过”的轨迹。
`/learn last` 调用当前 Provider 生成结构化建议，用户可以将其保存到 `.rook/memory/`、
忽略、标记运行时缺陷，或作为 quarantined Candidate 送入 Rook Forge。未确认或 stale
的项目记忆不会加载，Candidate 也不会自行激活。

## TUI Coding Workbench

![Rook Coding Workbench：Slash Command 面板与工具卡](docs/images/rook-tui-workbench.png)

| 输入 / 快捷键 | 作用 |
| --- | --- |
| `/status`、`/usage`、`/diff` | 查看项目状态、真实可观测用量和独立 Diff Viewer |
| `/copy last\|code\|selection\|transcript` | 复制回复、代码块、所选文本或完整会话 |
| `@src/app.py` | 安全引用带越界检查的项目文件快照 |
| `!git status` / 单独 `!` | 单次运行 / 切换受控 Shell，复用权限、审计和取消 |
| `Ctrl+R` | 搜索当前项目 Prompt 历史 |
| `Ctrl+X Ctrl+E` | 用 `$VISUAL` 或 `$EDITOR` 编辑 Prompt |
| `Shift+Tab` | 在 `ASK` 与 `AUTO` 间切换；`FULL` 使用 `/permissions` |
| 执行中 `Enter` / `Alt+Enter` | 引导当前任务 / 排队下一个独立任务 |

长工具输出按需展开。完整 Transcript 保留在状态层，界面只挂载最近 200 条。Rookie
继续作为启动吉祥物，发送第一条消息后自动让出工作区。

<p align="center">
  <img src="assets/rookie-mascot.png" alt="Rookie 菜鸟吉祥物" width="112">
</p>

## Review Agent 集成

Rook 可以调用独立运行的 [Review Agent](https://github.com/Lem0nTea2002/Review-Agent)。
在 `rook.toml` 保存服务 URL、项目别名和审阅器，管理员密码写入操作系统凭据库：

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

报告保留 `local-rules` 与 `open-code-review` 来源。应用选中的 Finding 会创建普通
Rook Coding Task，文件修改、Shell 与测试继续经过同一权限链路。

## 手机控制

本地 Gateway 接受已配对飞书或微信私聊发送的任务，并暂停等待一次敏感动作审批。
项目文件、模型凭据、Agent Loop、Skills 与工具执行全部留在电脑。

```powershell
pipx inject rook-agent "lark-oapi>=1.7,<2" "qrcode>=8,<9"
rook channel project add rook --path "D:\absolute\path\to\Rook"
rook channel setup feishu
rook channel login weixin
rook channel pair create --channel feishu --project rook
rook channel serve --channels feishu,weixin
```

入口只接受已配对用户的私聊文本和本机白名单项目。手机端只提供允许一次或拒绝，
五分钟超时按拒绝处理。

![Rook 手机渠道演示](docs/images/rook-mobile-demo.gif)

[完整配置与安全边界](docs/MOBILE_CHANNELS.zh-CN.md) ·
[脱敏真实验收证据](docs/evidence/rook-weixin-live-acceptance-2026-07-29.json)

## 项目记忆评测

评测让 Baseline 与 Memory 两臂使用相同 commit、工作区哈希、模型、工具 Schema、
容器和请求预算。stale、revoked 与未确认记录作为负控制保留；加载任一负控制都会
使证据失效。

| 阶段 | 当前证据 |
| --- | --- |
| M0 离线就绪 | 已验证运行时、脱敏、配对编排、ScoreCard 与稳定报告；Provider 调用为 0 |
| 最新 Pylint/Xarray Pilot | `pylint-dev__pylint-7114` 与 `pydata__xarray-3364`：Baseline 0% / Memory 0%；Xarray Memory Patch 破坏既有行为；Validator 成功臂为 0 |
| 20-pair Formal | 待执行；0% / 0% Pilot 不能作为简历指标 |

零模型调用验证公开目录：

```bash
rook benchmark memory verify --catalog benchmark/memory/v1/catalog.json
```

[M0 离线就绪](docs/benchmarks/MEMORY_M0_READINESS_2026-08-03.zh-CN.md) ·
[A/B 冻结设计](docs/benchmarks/MEMORY_AB_FREEZE_V1.zh-CN.md) ·
[Pilot 时间线](docs/benchmarks/MEMORY_PILOT_V1_2026-08-01.zh-CN.md)

## 配置

```bash
rook config setup
rook config init
rook config path
rook config show
```

Rook 支持 OpenAI、DeepSeek、Qwen、Moonshot、智谱、OpenRouter、Anthropic、Ollama
与自定义 OpenAI-compatible 接口。API Key 通过隐藏输入读取，并保存到操作系统凭据库。
环境变量可以覆盖已存凭据：

```bash
export OPENAI_API_KEY="your-openai-api-key"
export OPENAI_MODEL="gpt-4.1-mini"
```

主路径使用 OpenAI Chat Completions-compatible 接口，并实现 OpenAI-compatible 流式
输出与 `PROMPT_TOO_LONG` 等规范化错误。Rook 当前尚未使用 OpenAI Responses API，
原生 reasoning 与多模态属于后续能力。Anthropic Provider 仍为实验性，当前不提供
原生 thinking/cache/streaming。

## 架构

```text
rook_agent/
├── agent/          # Model → Tool → Model 执行循环
├── tools/          # 文件、搜索、编辑和命令工具
├── permissions/    # 权限策略与人工确认
├── context/        # 会话、上下文投影与压缩
├── providers/      # 模型 Provider
├── channels/       # 飞书/微信 Adapter、配对、队列与审批
├── evolution/      # 恢复检测、确认式记忆与 Candidate 分流
└── evalops/        # Rook Forge 考试与发布控制面
```

## 开发

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## 文档

- [技术文档入口](docs/README.zh-CN.md)
- [代码阅读指南](docs/CODEBASE_READING_GUIDE.zh-CN.md)
- [Agent Loop 安全边界](docs/AGENT_LOOP_GUARDRAILS.zh-CN.md)
- [Rook Forge 离线演示](docs/DEMO.md)
- [Issue → PR 演示](docs/ISSUE_TO_PR_DEMO.md)
- [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md) · [许可证](LICENSE)

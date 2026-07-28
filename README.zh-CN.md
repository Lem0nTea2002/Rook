<p align="center">
  <img src="assets/rookie-mascot.png" alt="Rookie 菜鸟吉祥物" width="160">
</p>

<h1 align="center">Rook</h1>

<p align="center">
  <strong>本地 Python Coding Agent，内置 Skill 考试、审批、部署与回滚。</strong>
</p>

<p align="center">
  <a href="https://github.com/Lem0nTea2002/Rook/releases/tag/v0.2.7"><img alt="Release v0.2.7" src="https://img.shields.io/badge/release-v0.2.7-56A8FF?style=flat-square"></a>
  <a href="https://github.com/Lem0nTea2002/Rook/actions/workflows/offline-tests.yml"><img alt="Offline CI" src="https://img.shields.io/badge/CI-Windows%20%7C%20Linux-61D095?style=flat-square"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <a href="README.md">English</a>
</p>

Rook 能在本地工作区读取和修改代码、调用工具、运行测试，并保存可恢复的会话。
内置的 **Rook Forge** 则负责判断一个 Skill 是否值得上线。

> Rook 负责完成 Coding Task；Rook Forge 负责 Skill 的考试、审批、部署与回滚。

## 演示

![Rook 动态演示：Rookie 启动页、Coding 工作流与 Skill 上线门禁](docs/images/rook-demo.gif)

## 快速开始

```bash
pipx install "git+https://github.com/Lem0nTea2002/Rook.git@v0.2.7"
rook
```

第一次交互启动时，Rook 会引导配置 Provider、模型、Base URL 和 API Key；
配置过程不会发送模型请求。

发送单条任务：

```bash
rook --message "读取这个项目，修复失败的测试"
```

不配置模型、零成本体验 Rook Forge：

```bash
rook eval demo
```

## 主要能力

| 能力 | 说明 |
| --- | --- |
| Coding Agent | 读取、搜索、修改代码，运行命令和测试 |
| 可见的 Agent Loop | 在 TUI 中展示流式输出、工具调用、结果和待办 |
| 权限与会话 | 高风险操作先确认；会话可持久化、恢复和压缩 |
| Skill 考试 | 隔离运行 Baseline、Forced 和 Routed 配对实验 |
| Skill 发布 | 自动门禁后仍需人工审批，支持 Rook/Codex 独立部署与回滚 |

## Rook Forge 如何工作

```text
Candidate 隔离
      ↓
Baseline / Forced / Routed 考试
      ↓
Evaluator + ScoreCard
      ↓
自动门禁 → 人工审批 → 按目标部署
      ↓
stale / drift 检测 → 原子回滚
```

自动门禁只决定“是否具备上线资格”，不会自动激活 Skill。安全失败、秘密泄漏、
新增回归、stale 或内容哈希不一致不能被人工绕过。

## TUI

每次启动 TUI 时，Rookie 都会出现在欢迎区；发送第一条消息后自动让出工作空间。

![带有 Rookie 的 Rook 启动界面](docs/images/rook-tui-welcome.png)

角色使用终端原生彩色字符绘制，不依赖 Kitty/Sixel 图片协议，在 Windows Terminal
和普通终端中都能稳定显示。截图由 Rook 的真实 Textual 组件离线渲染。

## 可信结果

| 验证 | 结果 |
| --- | --- |
| `gpt-5.4-mini` Formal | 72/72 次调用，36 个可比配对；Baseline 25% → Forced 94.4%（+69.4pp） |
| 效率与安全 | 中位时延 -5.8%，中位 Token -15.2%，新增回归 0，轨迹完整度 100% |
| 跨平台 CI | Ubuntu 1844 passed / 8 skipped；Windows 1845 passed / 7 skipped |
| 发布生命周期 | 真实完成门禁、人工审批、双目标部署、漂移检测与原子回滚 |

Formal 使用 sealed holdout；美元成本和 Codex 路由激活未观测，因此不做估算。
Fake Agent 演示只验证控制链路，不冒充真实模型效果。

详细证据：

- [简历证据合同](docs/PORTFOLIO_EVIDENCE.zh-CN.md)
- [Formal 结果](docs/PORTFOLIO_EVIDENCE.zh-CN.md#已完成的-candidate-v5--adapter-v12-formal)
- [真实发布生命周期](docs/evidence/rm2-v5-formal-release-2026-07-27.json)
- [完整仓库任务与规模执行](docs/FULL_REPO_AND_SCALE.md)

## 配置

```bash
rook config setup
rook config init
rook config path
rook config show
```

`rook config setup` 支持 OpenAI、DeepSeek、Qwen、Moonshot、智谱、
OpenRouter、Anthropic、Ollama 和自定义 OpenAI-compatible 接口。API Key
通过隐藏输入读取并保存到操作系统凭据库，不写入 `config.toml`。

环境变量优先于系统凭据和配置文件，例如：

```bash
export OPENAI_API_KEY="your-openai-api-key"
export OPENAI_MODEL="gpt-4.1-mini"
```

自定义 OpenAI-compatible Provider 默认使用 `ROOK_API_KEY`、
`ROOK_BASE_URL` 和 `ROOK_MODEL`。ChatGPT/Codex 订阅登录与 OpenAI API
认证相互独立，Rook 当前不会复用 Codex 登录。

配置文件：

```text
全局：~/.config/rook/config.toml
项目：./rook.toml
```

当前主线使用 OpenAI Chat Completions-compatible 接口和 OpenAI-compatible
流式实现，并规范化 `PROMPT_TOO_LONG` 等错误。Rook 尚未使用 OpenAI Responses
API，因此原生 reasoning 与多模态仍属于后续能力。Anthropic Provider 仍为
实验性，尚不提供原生 thinking/cache/streaming。

## 项目结构

```text
rook_agent/
├── agent/          # Model → Tool → Model 执行循环
├── tools/          # 文件、搜索、编辑和命令工具
├── permissions/    # 权限策略与人工确认
├── context/        # 会话、上下文投影与压缩
├── providers/      # 模型 Provider
└── evalops/        # Rook Forge 考试与发布控制面
```

## 文档

- [技术文档入口](docs/README.zh-CN.md)
- [代码阅读指南](docs/CODEBASE_READING_GUIDE.zh-CN.md)
- [Rook Forge 离线演示](docs/DEMO.md)
- [Issue → PR 演示](docs/ISSUE_TO_PR_DEMO.md)
- [技术文章：把 Skill 当成软件发布](docs/articles/ROOK_FORGE_FROM_SKILL_TO_RELEASE.zh-CN.md)

## 开发

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Rook 的目标很直接：做一个真实可运行、边界清楚，并且能够从头读懂的 Coding
Agent；再用 Rook Forge 让 Skill 上线有证据、可审计、能回滚。

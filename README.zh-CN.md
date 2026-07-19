<p align="center">
  <img src="assets/rook-logo.png" alt="Rook logo" width="156">
</p>

<h1 align="center">Rook</h1>

<p align="center">
  <strong>内置 Rook Forge Skill 考试、审批、部署与回滚的本地 Python Coding Agent。</strong>
</p>

<p align="center">
  <a href="#快速开始"><img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white"></a>
  <a href="#tui"><img alt="Textual TUI" src="https://img.shields.io/badge/Textual-TUI-5B5BD6?style=flat-square"></a>
  <a href="#配置"><img alt="OpenAI Compatible" src="https://img.shields.io/badge/OpenAI-Compatible-111827?style=flat-square"></a>
  <a href="#开发"><img alt="pytest" src="https://img.shields.io/badge/pytest-tested-0A9EDC?style=flat-square&logo=pytest&logoColor=white"></a>
</p>

<p align="center">
  <a href="README.md">English</a>
  · 简体中文
</p>

---

Rook 是一个能真实运行的本地 Python Coding Agent。**Rook Forge** 是它内置的 Skill 治理控制面：自动生成或人工编写的 Skill 先接受隔离配对考试和自动安全门禁，再经人工显式审批，按 Rook/Codex 目标独立部署，并通过不可变审计记录完成失效检测和回滚。实现包继续使用 `rook_agent.evalops`。

如果你想真正理解 coding agent 是怎么工作的，Rook 会尽量把关键环节展示出来，而不是把它们藏在黑盒后面。

- 在 Skill 获准部署前，客观评测它是否真正改善 Agent。
- 学习 agent loop、工具调用、权限系统、session 和上下文处理。
- 基于一个模块边界清晰的小型 Python 代码库继续改造。
- 一边使用本地 coding agent，一边读懂它的内部机制。

![Rook 规划、请求权限并完成本地任务](docs/images/rook-demo.gif)

## Rook Forge

Rook 将 Skill 视为必须经过考试和发布复核的版本化变更。人工 bundle 和轨迹生成结果先进入非活动隔离区，再执行 Baseline/Forced 与 Baseline/Routed 隔离配对实验；确定性 Evaluator 生成 ScoreCard，安全、回归、有效样本数和效果门槛按 Agent 目标独立决定是否具备审批资格。门禁通过后仍保持非活动，只有显式执行 `rook skill approve` 才会部署。

```mermaid
flowchart LR
    A["任务轨迹或人工 bundle"] --> B["Quarantined Candidate"]
    B --> C["隔离 Baseline / Forced / Routed 实验"]
    C --> D["Evaluator + ScoreCard"]
    D --> E{"自动门禁"}
    E -->|通过| F["具备资格，等待人工审批"]
    E -->|未通过| G["Rejected 或 Quarantined"]
    F --> H{"按目标人工审批"}
    H --> I["部署到 Rook 或仓库级 Codex"]
    I --> J["stale / drift 检测"]
    J --> K["原子回滚"]
```

版本化证据协议包含 12-case 开发/Pilot suite，以及完全不重叠、覆盖服务目录、应用、包、部署、运维和 ML 服务仓库形态的 12-case sealed Formal holdout。Formal manifest 锁定 Candidate content hash；Candidate 一旦变化，会在任何模型调用前失败。Fake Agent 控制实验只证明控制面正确。

修复原生 Windows 沙箱后，一轮获授权的 `gpt-5.4-mini` Pilot 完成 24/24 次调用和 12 个可比配对，基础设施排除 0、轨迹完整度 100%、新增回归 0；观测到 Baseline 25%、Forced Skill 100%（+75pp），中位时延降低 22.7%，中位 Token 降低 12.9%。该不可变轮次误用了 Formal 样本门槛，因此仍被隔离；现在已用独立 Pilot policy 修复边界。这些是 Pilot 测量，不是尚待授权的 72-call Formal 简历结论。

用一条命令运行从 Candidate 创建到双目标回滚的完整零成本生命周期：

```sh
rook eval demo
```

该命令只使用确定性 Fake Agent，并把 Registry、报告、Rook 部署和仓库级 Codex 部署全部隔离写入 `.rook/forge-demo/run-*`。它不会探测或启动 Codex，也不会产生模型或网络调用。

- [EvalOps 使用说明](docs/EVALOPS.md)
- [离线演示手册](docs/DEMO.md)
- [简历证据与表述边界](docs/PORTFOLIO_EVIDENCE.zh-CN.md)
- [Dogfooding 与事故记录](docs/DOGFOODING.md)
- [脱敏 Pilot 证据](docs/evidence/rm2-pilot-summary.json)

## 为什么做 Rook

大多数 coding-agent 演示展示的是表面：一个 prompt 进去，代码改完出来。Rook 关注的是中间的机械结构。

和 OpenCode 这类更大的项目相比，Rook 刻意把范围收得更小。

| 维度 | Rook | OpenCode 这类更大的项目 |
| --- | --- | --- |
| 主要目标 | 把 agent 内部机制做得可读、可学、可讲清楚 | 提供更完整、更偏产品化的 coding-agent 平台 |
| 代码形态 | 当前仓库核心运行时代码约 3.2 万行 Python | TS/JS 代码规模约 57 万行，平台层和工程表面也更多 |
| 工程取舍 | 主动放弃一部分额外平台能力，换取更强可读性 | 接受更高复杂度，以支持更宽的产品能力面 |
| 更适合谁 | 学习、二次改造、面试讲解、作品集 / 简历项目、本地实验 | 更想直接使用一个大而完整的 coding-agent 环境的用户 |

目标不是在功能数量上和更大的 coding agent 正面对抗，而是把系统做得既足够真实可用，又足够小，让你还能从头到尾读懂它，并理解每个子系统为什么存在。

这也意味着 Rook 很适合被深入学习、按自己的工作流继续改造，并在做出有代表性的扩展后，作为一个能写进简历或作品集的项目来展示。

和更偏教程型、轻量参考型的学习项目相比，Rook 也尽量保持它更像一个“小而完整、可验证”的工程系统。

| 维度 | Rook | 常见学习型 agent 项目 |
| --- | --- | --- |
| 学习价值 | 子系统边界清楚，文档明确，适合按模块阅读 | 往往更偏单一路径教程或 demo 流程 |
| 实用表面 | 有真实 TUI、tools、permissions、sessions、provider adapters | 往往更聚焦某个更窄的 loop 或概念验证 |
| 可验证性 | 有 120+ 个测试文件、跨平台离线 CI，并接入多个 benchmark 入口 | 往往较少强调测试体系和 benchmark 集成 |
| 延展路径 | 更适合继续改造成作品集或简历项目 | 更适合跟做和入门，但未必适合长期扩展 |

也就是说，这个仓库在“适合学习”之外，还尽量保留了足够的运行时结构、测试和 benchmark 钩子，让它在你第一次读完之后依然有继续演化的价值。

它适合这样的人：

- 想系统理解一个 coding agent 是如何组织起来的
- 想修改或扩展一个本地 Python 实现
- 想把 agent 架构真正看懂，并能在面试或学习中讲清楚

更细的子系统说明已经放进文档，这个 README 只保留项目首页需要的信息。

## 快速开始

推荐用 `pipx` 安装已打标签的 GitHub release：

```sh
pipx install "git+https://github.com/ZHUMUJUN/Rook.git@v0.2.1"
```

也可以从本地克隆目录安装：

```sh
pipx install .
```

启动 TUI：

```sh
rook
```

不打开 TUI，直接跑一轮消息：

```sh
rook --message "用一段话介绍这个仓库"
```

使用行式交互模式：

```sh
rook --interactive
```

无需配置 Provider、无需消耗模型 Token，直接体验 Rook Forge：

```sh
rook eval demo
```

## 你会得到什么

- 本地 Python coding agent
- 不隐藏 agent 活动状态的 Textual TUI
- 对危险操作先做权限确认的工具调用流程
- 会话持久化、恢复和上下文压缩
- 适合学习和二次开发的 skills、provider 和清晰模块结构
- Rook Forge Skill 隔离、A/B 考试、ScoreCard、人工审批、双目标部署和回滚

## 配置

创建初始配置：

```sh
rook config init
rook config path
rook config show
```

密钥建议放在环境变量里：

```sh
export ROOK_API_KEY="your-api-key"
```

默认配置路径：

```text
全局:  ~/.config/rook/config.toml
项目:  ./rook.toml
```

## TUI

Rook 的 TUI 不是为了把 agent loop 藏起来，而是为了把它展示出来。你可以在一个界面里看到 session 状态、流式输出、工具调用、工具结果和权限请求。

空闲状态：

![Rook 空闲状态](docs/images/rook-ready.png)

基础对话流：

![Rook 基础对话流](docs/images/tui-empty.png)

## 文档

- [技术文档入口](docs/README.zh-CN.md)
- [English Docs Index](docs/README.md)
- [代码阅读指南](docs/CODEBASE_READING_GUIDE.zh-CN.md)
- [Codex-only Skill EvalOps](docs/EVALOPS.md)
- [Rook Forge 离线演示](docs/DEMO.md)
- [简历证据说明](docs/PORTFOLIO_EVIDENCE.zh-CN.md)

## 开发

安装开发依赖：

```sh
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

运行全部测试：

```sh
.venv/bin/python -m pytest
```

运行单个测试文件：

```sh
.venv/bin/python -m pytest tests/test_app_tui.py -q
```

## 设计理念

Rook 想回答的是一个很多 coding agent 不会正面回答的问题：

> 当 agent 在流式输出、调用工具、申请权限、压缩上下文、恢复会话时，
> 内部到底发生了什么？

它是一个真实可运行的 agent，但它同样也是一个可以按子系统逐步读懂的 Python 项目。

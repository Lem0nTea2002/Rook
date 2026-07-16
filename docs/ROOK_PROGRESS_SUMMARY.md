# Rook 项目进度摘要

更新时间：2026-07-16

## 项目定位

Rook 是一个面向 Skill 的 Agent EvalOps 框架。它通过隔离执行、基线对照、工具轨迹分析和安全回归评测，判断一个自动生成或人工编写的 Skill 是否真正改善 Agent 的任务表现，并据此完成准入、拒绝和版本回滚。

第一版范围已经收敛为：

- Rook 自身作为进程内对照目标；
- Codex CLI 作为唯一外部 Agent；
- Claude Code 集成暂缓，后续通过现有 Adapter 扩展接口接入。

## 当前开发位置

- 分支：`feature/rook-forge`
- 工作树：`Rook/.worktrees/rook-forge`
- 最新范围调整提交：`697fc33 docs: narrow EvalOps MVP to Codex`

## 已完成功能

1. EvalOps 领域模型和严格的 TOML 评测套件加载。
2. Baseline/Candidate 隔离工作区及一致性校验。
3. 原始执行制品的脱敏、原子写入和路径安全控制。
4. Skill Candidate 的版本化存储、规范化渲染和隔离挂载。
5. Agent Adapter 通用接口、受控子进程边界和 Fake Agent。
6. Rook 进程内 EvalOps Adapter 与执行轨迹标准化。
7. Codex CLI Adapter、能力探测、安全环境策略和 JSONL 轨迹标准化。
8. 第一版实施计划已经调整为 Codex-only，不再阻塞于 Claude Code 适配。

## 关键提交

- `15bc922`：增加 Rook EvalOps Adapter。
- `d8df76b`：增加 Codex EvalOps Adapter 和 JSONL Normalizer。
- `697fc33`：将第一版 EvalOps 范围收敛为 Codex-only。

## 当前验证结果

- 已完成的 EvalOps Tasks 1-7：`234 passed, 5 skipped`（含 Windows CandidateStore 临时文件锁回归测试）。
- Codex Adapter 提交后专项验证：`58 passed, 1 skipped`。
- 默认测试全部使用 Fake Process/Fake Provider，不会调用真实 Codex API，也不会产生模型费用。
- Windows CandidateStore 已对短暂 `WinError 5` 进行有界重试，同时保持 no-replace 并发发布语义。

## 下一阶段计划

1. 实现确定性结果评测器和可选的受限 LLM Judge。
2. 编排 Baseline、Forced Skill 和 Routed Skill 三类隔离实验。
3. 生成成功率、成本、时延、路由精确率和路由召回率等 ScoreCard。
4. 实现 Skill 准入、拒绝、隔离、版本登记和回滚策略。
5. 提供 `rook eval`、`rook skill status` 和 `rook skill rollback` 等命令。
6. 接入执行轨迹驱动的 Skill Candidate 生成流程。
7. 增加可复现的 Codex 演示评测套件、可选真实冒烟测试和项目文档。

## 当前停点

当前停在 Task 9：确定性 Evaluator 与可选 LLM Judge。

评测器的接口和职责已经完成分析，但尚未修改 Task 9 的生产代码。后续可以从测试用例开始，按照 RED → GREEN → REFACTOR 的方式继续实现。

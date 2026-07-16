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
- 最新可演示 MVP 提交：`f23b4a7 feat: expose EvalOps CLI and deterministic demo`

## 已完成功能

1. EvalOps 领域模型和严格的 TOML 评测套件加载。
2. Baseline/Candidate 隔离工作区及一致性校验。
3. 原始执行制品的脱敏、原子写入和路径安全控制。
4. Skill Candidate 的版本化存储、规范化渲染和隔离挂载。
5. Agent Adapter 通用接口、受控子进程边界和 Fake Agent。
6. Rook 进程内 EvalOps Adapter 与执行轨迹标准化。
7. Codex CLI Adapter、能力探测、安全环境策略和 JSONL 轨迹标准化。
8. 第一版实施计划已经调整为 Codex-only，不再阻塞于 Claude Code 适配。
9. 确定性 Evaluator、单层组合评测和默认关闭的受限 LLM Judge。
10. Baseline/Forced/Routed 两组独立配对实验、交替顺序、状态优先级和终态制品。
11. ScoreCard、Wilson 区间、内容准入与独立路由判定。
12. 不可变决策历史、按 target 活动指针、stale 检测、原子 rollback 和稳定报告。
13. `rook eval` / `rook skill` CLI、四类确定性 demo suite 和默认跳过的真实 Codex smoke。

## 关键提交

- `15bc922`：增加 Rook EvalOps Adapter。
- `d8df76b`：增加 Codex EvalOps Adapter 和 JSONL Normalizer。
- `697fc33`：将第一版 EvalOps 范围收敛为 Codex-only。
- `45d8834`：增加受限且可选的 LLM Judge。
- `6941bfe`：增加隔离配对实验编排。
- `0260af9`：增加 ScoreCard 与 Skill 准入策略。
- `993c44f`：增加 Registry、报告和端到端 EvalOpsService。
- `f23b4a7`：增加 EvalOps CLI、确定性 demo 和真实 smoke 授权边界。

## 当前验证结果

- 当前全部 `test_evalops_*` 专项：`327 passed, 7 skipped`。
- CLI、配置、品牌和 README 直接回归：`47 passed`。
- Codex Adapter 提交后专项验证：`58 passed, 1 skipped`。
- 默认测试全部使用 Fake Process/Fake Provider，不会调用真实 Codex API，也不会产生模型费用。
- Windows CandidateStore 已对短暂 `WinError 5` 进行有界重试，同时保持 no-replace 并发发布语义。
- 真实 Codex smoke 仍由 `ROOK_RUN_EXTERNAL_EVALS=1` 控制，并额外要求 `ROOK_ALLOW_MODEL_COSTS=1`；本轮保持 skipped。

## 下一阶段计划

1. 接入执行轨迹驱动、EvidenceRef 可追溯的 Skill Candidate 生成流程。
2. Candidate 只以 candidate/quarantined 状态进入 CandidateStore，并重新进入同一 EvalOps 准入链路。
3. 完成 Task 16 全量回归、安全硬化、工作树卫生和最终文档一致性验证。

## 当前停点

当前停在 Task 14：从执行轨迹提炼 EvidenceRef 可追溯的 quarantined Skill Candidate。

手工 Candidate 的 Candidate → A/B → ScoreCard → Decision → Registry → Report → Rollback 闭环已经可演示。下一步不建立旁路准入机制，自动生成的 Candidate 仍复用现有评测、准入和回滚链路。

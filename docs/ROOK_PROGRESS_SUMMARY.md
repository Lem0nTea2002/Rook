# Rook 项目进度摘要

更新时间：2026-07-17

## 项目定位

Rook 是一个面向 Skill 的 Agent EvalOps 框架。它通过隔离执行、基线对照、工具轨迹分析和安全回归评测，判断一个自动生成或人工编写的 Skill 是否真正改善 Agent 的任务表现，并据此完成准入、拒绝和版本回滚。

第一版范围已经收敛为：

- Rook 自身作为进程内对照目标；
- Codex CLI 作为唯一外部 Agent；
- Claude Code 集成暂缓，后续通过现有 Adapter 扩展接口接入。

## 当前开发位置

- 分支：`feature/rook-forge`
- 工作树：`Rook/.worktrees/rook-forge`
- 最新 RM-2 套件提交：`6d41dd9 feat: add RM-2 differential Skill benchmark`

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
14. 严格、脱敏且 EvidenceRef 可追溯的轨迹蒸馏器。
15. 自动 Candidate 的 `quarantined` 隔离存储、安全 Gate、幂等生命周期协调和 Provider 切换。
16. 自动 Candidate 继续复用显式 EvalOps 准入链路，不自动发布、发现、激活或导出。
17. Windows/Linux 双平台离线 CI，显式关闭真实外部评测和模型费用。
18. 严格人工 Skill bundle loader 与 `rook skill stage`，导入结果默认保持 `imported/quarantined`。
19. 12-case 简历证据 suite，以及有效、中性、危险三类控制 Candidate 的准入/拒绝证明。
20. `content/routing/both` 实验选择、`auto/fast/full` 阶段控制和不修改 Registry 的 measurement-only 模式。
21. Direct/Transfer 能力指标与 Regression/Adversarial 保持性指标分层，包含 Wilson 区间和固定种子的任务分层 bootstrap。
22. RM-2 差异化正式套件、隐藏语义 Validator、12/24/72 调用边界及 Calibration/Formal 策略。

## 关键提交

- `15bc922`：增加 Rook EvalOps Adapter。
- `d8df76b`：增加 Codex EvalOps Adapter 和 JSONL Normalizer。
- `697fc33`：将第一版 EvalOps 范围收敛为 Codex-only。
- `45d8834`：增加受限且可选的 LLM Judge。
- `6941bfe`：增加隔离配对实验编排。
- `0260af9`：增加 ScoreCard 与 Skill 准入策略。
- `993c44f`：增加 Registry、报告和端到端 EvalOpsService。
- `f23b4a7`：增加 EvalOps CLI、确定性 demo 和真实 smoke 授权边界。
- `bac7ec1`：增加执行轨迹驱动、严格证据绑定的 quarantined Candidate 生成流程。
- `116b04f`：增强 Windows 临时 Candidate 清理并保留原始并发冲突语义。
- `5a94aac`：修复父级 Git 仓库误识别、并发测试时序抖动和过期 prompt 断言。
- `5331a9a`：使 ChainSWE verifier 可在 Windows 使用 Git for Windows shell，并避免已知任务序列的额外边界模型调用。
- `a94a531`：增加 Windows/Linux 完整离线测试门禁。
- `fba8b68`：增加严格人工 Skill bundle staging，默认非活动隔离存储。
- `b085dea`：增加 12-case EvalOps 简历证据套件和三类控制 Candidate。
- `b7c246b`：增加有界实验 family、phase 和 measurement-only 控制。
- `4dee29f`：增加能力/保持性分层 ScoreCard 与正式门禁。
- `6d41dd9`：增加 RM-2 差异化 Skill 基准、隐藏 Validator 和分阶段策略。

## 当前验证结果

- 当前全部 EvalOps + evolution 专项：`626 passed, 7 skipped`。
- 当前全部 EvalOps 专项：`383 passed, 7 skipped`。
- Task 14 新增与直接依赖专项：`323 passed`。
- Windows/安全硬化专项：`295 passed, 3 skipped`。
- 历史失败维护直接回归：`131 passed`。
- 完整核心基线（排除可选 EvalPlus）：`1596 passed, 10 skipped`，零失败。
- RM-2 离线控制实验：有效 Candidate `promoted`、中性 Candidate `rejected`、危险 Candidate 因 3 个 adversarial 新增回归而 `rejected`；仅证明控制面，不作为真实模型效果。
- RM-2 调用数已静态验证：Calibration `12`、Pilot `24`、Formal `72`。
- CLI、配置、品牌和 README 直接回归：`47 passed`。
- Codex Adapter 提交后专项验证：`58 passed, 1 skipped`。
- 默认测试全部使用 Fake Process/Fake Provider，不会调用真实 Codex API，也不会产生模型费用。
- Windows CandidateStore 已对短暂 `WinError 5` 进行有界重试，同时保持 no-replace 并发发布语义。
- 真实 Codex smoke 仍由 `ROOK_RUN_EXTERNAL_EVALS=1` 控制，并额外要求 `ROOK_ALLOW_MODEL_COSTS=1`；本轮保持 skipped。

## 下一阶段计划

1. 推送分支，让新增的 Windows/Linux GitHub Actions 门禁完成远端首跑。
2. 单独获得 12 次外部调用与费用授权后运行 RM-2 Calibration；通过后再分别申请 24 次 Pilot 和 72 次 Formal 授权。
3. 可选安装 `evalplus` 并运行独立 benchmark gate；它不阻塞 Codex-only MVP。
4. 审阅并合并 `feature/rook-forge`。
5. 合并后再独立评估 Candidate 蒸馏后台队列，避免把额外并发复杂度带入当前 MVP。

## 当前停点

RM-2 的代码、隐藏 Validator、分层 ScoreCard、正式门禁和零成本控制均已完成。工程闭环已经具备可验证表述；真实 Agent 成功率、Token、时延与成本仍停在 12 次 Calibration 的单独显式授权前，当前没有用 Fake 结果填充这些指标。

手工与自动 Candidate 均已接入 Candidate → A/B → ScoreCard → Decision → Registry → Report → Rollback 的同一闭环。自动生成结果保持 quarantined，必须显式执行评测；当前没有旁路准入机制。完整验证证据见 `docs/superpowers/reports/2026-07-16-rook-agent-evalops-verification.md`。

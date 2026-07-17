# Rook 项目进度摘要

更新时间：2026-07-17

## 项目定位

Rook 是一个可真实运行的本地 Python Coding Agent；Rook Forge 是内置的 Skill 考试、上线审批、部署与版本回滚控制面。Forge 通过隔离执行、基线对照、工具轨迹和安全回归判断 Candidate 是否具备上线资格，但自动门禁不会直接激活 Skill，必须经过按目标独立、不可变的人工审批。

第一版范围已经收敛为：

- Rook 自身作为进程内执行与部署目标；
- Codex CLI 作为唯一外部评测和仓库级部署目标；
- Claude Code 集成暂缓，后续通过现有 Adapter 扩展接口接入。

## 当前开发位置

- 分支：`feature/rook-forge`
- 工作树：`Rook/.worktrees/rook-forge`
- 当前产品化改动：工作树中待提交，包含 Registry v2、人工审批、双目标发布和 `/forge`。

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
12. 不可变自动门禁历史、按 target 资格指针、stale 检测和稳定报告。
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
23. Registry v2 将 `eligible_targets` 与 `deployed_targets` 分离；v1 历史活动指针只迁移为 eligible，不会在升级后自动部署。
24. `ApprovalRecord`、`ReleaseRecord`、`DeploymentReceipt` 和失败发布审计；安全失败、秘密泄漏、新增回归、stale 与 hash mismatch 不能被人工绕过。
25. Rook/Codex 按目标独立审批和回滚；Rook Runtime 只发现已部署版本，Codex 只写当前仓库 `.agents/skills/<name>`。
26. Codex 发布采用每 Skill 文件锁、同级 staging/backup、事务 journal、崩溃恢复、漂移检测和 Windows 临时文件占用有界重试；不覆盖非 Rook 管理目录。
27. 新增 `rook skill approve/history`，升级 status/rollback/export，并提供只读 `/forge` 状态页。
28. Codex EvalOps 显式设置 `web_search="disabled"` 与 `sandbox_workspace_write.network_access=false`；禁网运行出现 Web Search 会成为安全策略违规。

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
- `28c2575`：增加 RM-2 Fake 控制、报告断言、调用数证明和证据文档。

## 当前验证结果

- 当前 Rook Forge/EvalOps 专项：`439 passed, 7 skipped`。
- 当前 EvalOps + evolution 联合专项：`669 passed, 7 skipped`。
- 当前完整核心离线基线（排除可选 `evalplus` benchmark）：`1672 passed, 10 skipped`，用时 `360.55s`；运行时显式关闭外部评测和模型费用。
- RM-2 离线控制实验：有效 Candidate `promoted`、中性 Candidate `rejected`、危险 Candidate 因 3 个 adversarial 新增回归而 `rejected`；仅证明控制面，不作为真实模型效果。
- RM-2 调用数已静态验证：Calibration `12`、Pilot `24`、Formal `72`。
- CLI、配置、品牌和 README 直接回归：`47 passed`。
- Codex Adapter 提交后专项验证：`58 passed, 1 skipped`。
- 默认测试全部使用 Fake Process/Fake Provider，不会调用真实 Codex API，也不会产生模型费用。
- Windows CandidateStore 已对短暂 `WinError 5` 进行有界重试，同时保持 no-replace 并发发布语义。
- 真实 Codex smoke 仍由 `ROOK_RUN_EXTERNAL_EVALS=1` 控制，并额外要求 `ROOK_ALLOW_MODEL_COSTS=1`；本轮产品化验证显式设置为 `0`，保持 skipped。
- 已授权的 RM-2 Calibration 报告 `evaluation-7b656409ddb54076a36cddf7822659fd` 形成 5 个完整可比配对：Baseline 20%、Forced Skill 100%（+80pp），中位时延降低 27.4%，完整 Token 观测的中位数增加 17.2%，Preservation 2/2、无新增回归。
- 上述 Calibration 有 1 个基础设施排除、轨迹完整度 80%，最终为 `quarantined (excess_infrastructure_exclusions)`；它不能作为上线或 Formal 简历结论，美元成本仍未观测。

## 下一阶段计划

1. 推送分支，让 Windows/Linux GitHub Actions 门禁验证 Registry v2 和事务发布。
2. 修正 Calibration 基础设施排除后，重新单独申请 12 次 Calibration；通过后再分别申请 24 次 Pilot 和 72 次 Formal 授权。
3. 可选安装 `evalplus` 并运行独立 benchmark gate；它不阻塞 Codex-only MVP。
4. 审阅并合并 `feature/rook-forge`。

## 当前停点

Rook Forge 产品闭环已经形成：Candidate → 隔离考试 → ScoreCard → 自动门禁 → 人工审批 → Rook/Codex 独立部署 → stale/drift 检测 → 原子回滚。自动门禁通过后保持 inactive，只有 approve 才会进入运行时或仓库级 Codex Skill 目录。

手工与自动 Candidate 共用同一条治理链路，自动生成结果保持 quarantined，当前没有旁路准入机制。现有 Calibration 已如实记录但因基础设施排除被隔离；最终简历成功率、Token 和时延仍必须等待 72-call Formal，成本在 Codex 不提供费用字段时继续写 `not observed`。

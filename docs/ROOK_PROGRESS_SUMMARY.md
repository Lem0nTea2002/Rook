# Rook 项目进度摘要

更新时间：2026-07-19

## 项目定位

Rook 是一个可真实运行的本地 Python Coding Agent；Rook Forge 是内置的 Skill 考试、上线审批、部署与版本回滚控制面。Forge 通过隔离执行、基线对照、工具轨迹和安全回归判断 Candidate 是否具备上线资格，但自动门禁不会直接激活 Skill，必须经过按目标独立、不可变的人工审批。

第一版范围已经收敛为：

- Rook 自身作为进程内执行与部署目标；
- Codex CLI 作为唯一外部评测和仓库级部署目标；
- Claude Code 集成暂缓，后续通过现有 Adapter 扩展接口接入。

## 当前开发位置

- 分支：`agent/rook-v0.2.1-evidence`
- 工作树：`D:/WorkAndStudy/FindJob/New-Harness-Agent/Rook`
- Rook Forge 产品化主线已通过 PR #1 合并到 `main`，合并提交为 `6f6a9d8`。
- 当前改动：v0.2.1 发布候选，包含 Windows 沙箱修复、独立 Pilot policy、sealed Formal holdout、长期趋势视图和质量/供应链门禁。

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
29. `rook eval demo` 使用打包的四类 Suite 和 Fake Agent，在独立 run 目录内完整演示 Candidate → 门禁 → 人工审批 → Rook/Codex 部署 → v2 替换 → 双目标原子回滚。
30. 补齐 `CHANGELOG.md`、`SECURITY.md`、`CONTRIBUTING.md`、中英双语 Demo 手册和已安装 wheel 冒烟；GitHub Actions 升级到当前 Node 24 主版本。
31. 修复原生 Windows Codex 0.144.x 工作区写入边界：移除分裂的嵌套临时写入根、禁止工具侧 `apply_patch`，并为 Calibration/Pilot/Formal 增加明确、独立的策略边界。
32. Formal 改用与 Pilot case ID、fixture 内容完全不重叠的 12-case sealed holdout，并以 Candidate content hash 在任何模型调用前 fail closed。
33. 新增 `rook eval trends`，对不可变脱敏报告输出版本趋势、fingerprint 边界、失败类型、SLO、门禁与部署/回滚历史。
34. CI 升级为 Windows/Ubuntu × Python 3.11/3.12 矩阵，并增加 Ruff、mypy、EvalOps 覆盖率阈值、pip-audit 和 Dependabot。
35. 增加版本化、脱敏的 Pilot 证据摘要和 dogfooding/事故账本；明确区分真实模型测量、Fake Agent 控制实验和尚未运行的 Formal。

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

- 当前 EvalOps 离线覆盖率门禁：`461 passed, 7 skipped`，总覆盖率 `85.84%`；新增边界测试使 Ubuntu 也能独立满足 85% 门槛。
- 当前本地完整核心离线基线（排除可选 `evalplus` benchmark）：`1708 passed, 10 skipped`，用时 `382.86s`；运行时显式关闭外部评测和模型费用。
- Ruff 全仓关键规则、mypy 核心 EvalOps 边界和 pip-audit 均通过；pip-audit 未发现已知第三方依赖漏洞，本地未发布包按预期标记为不可从 PyPI 审计。
- `rook-agent 0.2.1` wheel/sdist 已实际构建；wheel 在全新临时虚拟环境中完成安装、版本导入、`rook --help` 和 `rook eval demo`，双目标部署/替换/回滚全链路通过。
- v0.2.1 wheel SHA-256 为 `eccd8aa29c91057746d4e51397669d7f8539dd16c667a0da9df213e98367a8a0`；sdist SHA-256 为 `6b876f2a7ebb62e82a73447df2fe8dcddd0cdbdcd1c010718e12185dacf8a07b`。
- GitHub Actions Python 3.11 双平台门禁 [run 29583269292](https://github.com/ZHUMUJUN/Rook/actions/runs/29583269292) 全绿：Windows `1679 passed, 6 skipped`，Ubuntu `1678 passed, 7 skipped`；两端均显式关闭真实 Codex 和模型费用。
- RM-2 离线控制实验：有效 Candidate `promoted`、中性 Candidate `rejected`、危险 Candidate 因 3 个 adversarial 新增回归而 `rejected`；仅证明控制面，不作为真实模型效果。
- RM-2 调用数已静态验证：Calibration `12`、Pilot `24`、Formal `72`。
- CLI、配置、品牌和 README 直接回归：`47 passed`。
- Codex Adapter 提交后专项验证：`58 passed, 1 skipped`。
- 默认测试全部使用 Fake Process/Fake Provider，不会调用真实 Codex API，也不会产生模型费用。
- Windows CandidateStore 已对短暂 `WinError 5` 进行有界重试，同时保持 no-replace 并发发布语义。
- 真实 Codex smoke 仍由 `ROOK_RUN_EXTERNAL_EVALS=1` 控制，并额外要求 `ROOK_ALLOW_MODEL_COSTS=1`；本轮产品化验证显式设置为 `0`，保持 skipped。
- 已授权的 RM-2 Calibration 报告 `evaluation-7b656409ddb54076a36cddf7822659fd` 形成 5 个完整可比配对：Baseline 20%、Forced Skill 100%（+80pp），中位时延降低 27.4%，完整 Token 观测的中位数增加 17.2%，Preservation 2/2、无新增回归。
- 上述 Calibration 有 1 个基础设施排除、轨迹完整度 80%，最终为 `quarantined (excess_infrastructure_exclusions)`；它不能作为上线或 Formal 简历结论，美元成本仍未观测。
- Windows 修复后 2-call strict smoke 为 2/2 成功，均正常结束且无 `apply_patch`、分裂写入根、超时或沙箱写入错误。
- 已授权的 24-call Pilot 测量报告 `evaluation-5eef9bb282934e9e8748221ce9e24e2d` 完成 12/12 配对，基础设施排除 0、轨迹完整度 100%；Baseline 25%、Forced Skill 100%（+75pp），中位时延降低 22.7%，中位 Token 降低 12.9%，Preservation 6/6、无新增回归。
- 该 Pilot 误用了 Formal policy，因只有 6 个能力配对而被不可变报告标记为 `quarantined (insufficient_valid_pairs)`；已增加独立 `pilot.toml` / `rm2-pilot.toml`。对同一指标进行不落盘阈值模拟的结果为 `promoted (capability_success_uplift)`，但原报告不会被改名或重评分。
- 新 Formal holdout 仍为 12 case × 3 repetition × 2 arm = 72 次调用，但其 case ID 和 fixture SHA-256 与 Pilot 完全不重叠；Candidate hash 已冻结，Formal 尚未授权、尚未运行。

## 下一阶段计划

1. 提交并推送 v0.2.1，跑通 Windows/Linux × Python 3.11/3.12 远端 CI，发布 GitHub Release 并验证公开安装入口。
2. 如需填写最终简历模型效果，再单独申请 72 次 Formal 授权；使用 sealed `suite.toml`、3 次重复，不复用 Pilot 授权。
3. 在不伪造记录的前提下继续累积 3–5 个真实 Skill 的 gate、审批、部署、drift 和 rollback 生命周期。
4. 可选安装 `evalplus` 并运行独立 benchmark gate；它不阻塞 Codex-only MVP。

## 当前停点

Rook Forge 产品闭环已经形成，并可由 `rook eval demo` 零配置复现：Candidate → 隔离考试 → ScoreCard → 自动门禁 → 人工审批 → Rook/Codex 独立部署 → stale/drift 检测 → 原子回滚。自动门禁通过后保持 inactive，只有 approve 才会进入运行时或仓库级 Codex Skill 目录。

手工与自动 Candidate 共用同一条治理链路，自动生成结果保持 quarantined，当前没有旁路准入机制。Windows 沙箱基础设施问题已由 2-call strict smoke 和 24-call Pilot 的 0 排除、100% 完整轨迹验证；Pilot 显示明确正向效果，但最终简历成功率、Token 和时延仍必须等待单独授权的 72-call Formal。Codex 不提供费用字段时，成本继续写 `not observed`。

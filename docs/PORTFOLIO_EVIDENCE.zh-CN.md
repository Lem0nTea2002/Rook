# Rook 简历证据说明

本文把“已经由代码和离线测试证明的工程事实”与“必须经过授权真实评测才能填写的模型效果”分开，避免在简历中把 Fake Agent 结果写成真实提升。

## 问题与系统边界

自动生成或人工编写的 Skill 不能因为偶然完成一次任务就直接激活。Rook 将 Candidate 放入非活动隔离区，执行 Baseline/Forced 与 Baseline/Routed 配对实验，归一化 Agent 轨迹，运行确定性 Evaluator，生成 ScoreCard，并按目标 Agent 记录准入或拒绝决策，同时支持 stale 检测和原子回滚。

原有 Rook Runtime 提供交互 Agent、工具、权限、会话和上下文管理；EvalOps 扩展提供版本化 suite、隔离工作区与制品、Rook/Codex Adapter、Evaluator、实验编排、评分策略、Registry、报告、CLI，以及轨迹驱动的 quarantined Candidate。

## 无模型调用即可复现的证据

| 证据 | 当前结果 |
| --- | --- |
| 完整离线核心测试 | 1,500+ 通过；精确数字记录在 `docs/ROOK_PROGRESS_SUMMARY.md` |
| 操作系统 | 已配置 Windows/Linux GitHub Actions 矩阵 |
| 简历证据 suite | 12 个案例：Direct、Transfer、Regression、Adversarial 各 3 个 |
| 有效控制 Candidate | 确定性 Fake Agent 控制实验中 promoted |
| 中性控制 Candidate | 因无可测提升被 rejected |
| 危险控制 Candidate | 因 adversarial 回归被 rejected |
| 控制实验外部调用 | 0 |

复现控制实验：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_portfolio.py
```

将三个手工版本放入隔离区，但不激活：

```powershell
rook skill stage --bundle evals\candidates\release-manifest\effective.toml
rook skill stage --bundle evals\candidates\release-manifest\neutral.toml
rook skill stage --bundle evals\candidates\release-manifest\unsafe.toml
rook skill status release-manifest-normalizer
```

## 真实评测填写合同

以下字段不能用估算值替代。只有在显式授权外部调用和费用，并生成不可变报告后才能填写。

| 指标 | 必需证据 | 当前值 |
| --- | --- | --- |
| 有效配对样本数 | 排除基础设施失败后的 Direct/Transfer 配对 | 未测量 |
| Baseline 成功率 | Baseline passed / 有效 Baseline | 未测量 |
| Forced Skill 成功率 | Forced passed / 有效 Forced | 未测量 |
| 配对成功率提升 | Forced 减配对 Baseline，并附 Wilson 95% 区间 | 未测量 |
| 新增回归 | Baseline 通过但 Candidate 失败的 Regression/Adversarial 案例 | 未测量 |
| 中位时延变化 | 配对毫秒中位数 | 未测量 |
| Token 变化 | 可观测输入/输出 Token 配对值 | 未测量 |
| 成本变化 | 可观测模型费用配对值 | 未测量 |
| 路由 precision/recall | 只能来自可靠的 `skill_loaded` 身份事件 | Codex 未观测 |

12 个案例至少重复 3 次。发布任何指标时，必须同时记录 suite fingerprint、policy fingerprint、目标模型版本、重复次数、基础设施排除项、不可变报告路径和授权状态。

正式执行使用 `rook eval run --model <model>` 显式指定 Codex 模型；可选 live smoke 使用 `ROOK_CODEX_EVAL_MODEL`。模型会进入目标指纹，不能依赖被隔离执行忽略的用户配置。

## 简历表述边界

现在可以写：

> 设计并实现 Codex-only Skill EvalOps 框架，支持隔离配对实验、确定性评测、ScoreCard、quarantine、按 Agent 独立准入、stale 检测、回滚和跨平台离线测试门禁。

真实报告生成前不能写：

> 真实 Agent 任务成功率提升 X%，成本下降 Y%。

Fake Agent 的准入/拒绝只证明控制面正确，不能作为真实模型效果。

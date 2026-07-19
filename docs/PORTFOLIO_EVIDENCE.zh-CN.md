# Rook 简历证据说明

本文把“已经由代码和离线测试证明的工程事实”与“必须经过授权真实评测才能填写的模型效果”分开，避免在简历中把 Fake Agent 结果写成真实提升。

## 问题与系统边界

自动生成或人工编写的 Skill 不能因为偶然完成一次任务就直接激活。Rook Forge 将 Candidate 放入非活动隔离区，执行 Baseline/Forced 与 Baseline/Routed 配对实验，归一化 Agent 轨迹，运行确定性 Evaluator 并生成 ScoreCard。自动门禁只产生 `promoted/rejected/quarantined` 资格结论；通过后还必须按 Rook/Codex 目标分别接受不可变人工审批，才能部署，并受 stale、drift 和原子回滚保护。

原有 Rook Runtime 提供交互 Agent、工具、权限、会话和上下文管理；EvalOps 扩展提供版本化 suite、隔离工作区与制品、Rook/Codex Adapter、Evaluator、实验编排、评分策略、Registry、报告、CLI，以及轨迹驱动的 quarantined Candidate。

## 无模型调用即可复现的证据

| 证据 | 当前结果 |
| --- | --- |
| 完整离线核心测试 | 1,500+ 通过；精确数字记录在 `docs/ROOK_PROGRESS_SUMMARY.md` |
| 操作系统 | 已配置 Windows/Linux GitHub Actions 矩阵 |
| RM-2 证据 suite | 12 个版本化案例：Direct、Transfer、Regression、Adversarial 各 3 个 |
| Sealed Formal holdout | 12 个不重叠案例，覆盖六种仓库形态；执行前锁定 Candidate SHA-256 |
| 有效控制 Candidate | 确定性 Fake Agent 控制实验中 promoted |
| 中性控制 Candidate | 因无可测提升被 rejected |
| 危险控制 Candidate | 因 3 个 adversarial 保持性回归被 rejected |
| 已授权 Calibration | 12 次计划调用；形成 5 个完整可比配对，结论 quarantined |
| 已授权 Pilot 测量 | 24/24 次调用完成；12 个完整配对；基础设施排除 0 |
| 剩余真实调用计划 | Formal 72 次，必须重新显式授权 |
| 控制实验外部调用 | 0 |

复现控制实验：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_rm2.py tests/test_evalops_portfolio.py
```

将三个手工版本放入隔离区，但不激活：

```powershell
rook skill stage --bundle evals\candidates\release-manifest-v2\effective.toml
rook skill stage --bundle evals\candidates\release-manifest-v2\neutral.toml
rook skill stage --bundle evals\candidates\release-manifest-v2\unsafe.toml
rook skill status release-manifest-v2-normalizer
```

## 已完成的 Calibration（不能作为 Formal 结论）

不可变报告：`.rook/evalops/artifacts/reports/evaluation-7b656409ddb54076a36cddf7822659fd/scorecard.json`。目标为 Codex CLI `0.144.1`、`gpt-5.4-mini`，Candidate 为 `release-manifest-v2-normalizer@1`。

| 指标 | Baseline | Forced Skill | 变化 |
| --- | ---: | ---: | ---: |
| 完整可比配对成功率，n=5 | 20% | 100% | +80pp |
| 能力任务成功率，n=3 | 0% | 100% | +100pp |
| 中位时延 | 107.686s | 78.188s | 降低 27.4% |
| 能力任务中位时延 | 120.171s | 78.188s | 降低 34.9% |
| 中位 Token，完整观测 n=3 | 76,914 | 90,109 | 增加 17.2% |
| Preservation | — | 2/2 | 新增回归 0 |
| 美元成本 | 未观测 | 未观测 | 无法计算 |

该轮有 1 个基础设施排除、轨迹完整度 80%，最终门禁为 `quarantined (excess_infrastructure_exclusions)`。因此这些数字证明“该套件能测出差异”，不证明 Candidate 已具备上线资格，也不能充当 72-call Formal 简历指标。

## 已完成的 24-call Pilot 测量（不能作为 Formal 结论）

不可变报告：`.rook/evalops/artifacts/reports/evaluation-5eef9bb282934e9e8748221ce9e24e2d/scorecard.json`。该授权轮次使用 Codex CLI `0.144.1` 与 `gpt-5.4-mini`，12 组 Baseline/Forced 配对全部完成。

| 指标 | Baseline | Forced Skill | 变化 |
| --- | ---: | ---: | ---: |
| 完整可比配对成功率，n=12 | 25% | 100% | +75pp |
| 能力任务成功率，n=6 | 0% | 100% | +100pp；bootstrap 95% 区间 [100pp, 100pp] |
| 中位时延 | 77.469s | 59.898s | 降低 22.7% |
| 能力任务中位时延 | 80.616s | 67.852s | 降低 15.8% |
| 中位 Token | 49,749 | 43,350 | 降低 12.9% |
| 能力任务中位 Token | 49,749 | 52,042 | 增加 4.6% |
| Preservation | — | 6/6 | 新增回归 0 |
| 基础设施 / 轨迹 | — | 排除 0 | 完整度 100% |
| 美元成本 | 未观测 | 未观测 | 无法计算 |

调用和测量数据有效，但该轮误用了 Formal manifest，所以不可变自动门禁结果为 `quarantined (insufficient_valid_pairs)`：Pilot 的一次重复只有 6 个能力配对，Formal 策略要求 18 个。现在已增加独立的 `pilot.toml` 与 `rm2-pilot.toml`，防止以后把 24-call Pilot 套用 72-call 样本门槛；既有不可变证据不会被改名或静默重评分。这些 Pilot 数值可作为工程验证证据，但不是最终简历效果结论。

## Formal 真实评测填写合同

以下字段不能用估算值替代。只有在显式授权外部调用和费用，并生成不可变报告后才能填写。

| 指标 | 必需证据 | 当前值 |
| --- | --- | --- |
| 能力配对样本数 | 排除基础设施失败后的 Direct/Transfer 配对 | Formal 未测量 |
| Baseline 成功率 | Baseline passed / 有效 Baseline | Formal 未测量 |
| Forced Skill 成功率 | Forced passed / 有效 Forced | Formal 未测量 |
| 配对成功率提升 | Forced-Baseline 的配对均值，并附任务分层 bootstrap 95% 区间 | Formal 未测量 |
| 新增回归 | Baseline 通过但 Candidate 失败的 Regression/Adversarial 案例 | Formal 未测量 |
| 中位时延变化 | 配对毫秒中位数 | Formal 未测量 |
| Token 变化 | 可观测输入/输出 Token 配对值 | Formal 未测量 |
| 成本变化 | 可观测模型费用配对值 | Formal 未测量 |
| 路由 precision/recall | 只能来自可靠的 `skill_loaded` 身份事件 | Codex 未观测 |

真实协议分为 12 次 Calibration（`calibration.toml`）、24 次 Pilot（`pilot.toml`）和 72 次 Formal（sealed 且与 Pilot 不重叠的 `suite.toml`，12 案例 x 3 次重复 x 2 个实验臂）。Formal manifest 锁定 Candidate content hash；一旦变化，会在 Agent 调用前失败。每一阶段都需要单独显式授权，并在进入下一阶段前暂停。发布任何指标时，必须同时记录 suite fingerprint、policy fingerprint、目标模型版本、重复次数、基础设施排除项、不可变报告路径和授权状态。

正式执行使用 `rook eval run --model <model>` 显式指定 Codex 模型；可选 live smoke 使用 `ROOK_CODEX_EVAL_MODEL`。模型会进入目标指纹，不能依赖被隔离执行忽略的用户配置。

## 简历表述边界

现在可以写：

> 设计并实现 Rook Forge Skill 治理控制面，支持隔离配对实验、确定性评测、ScoreCard、quarantine、自动门禁、按 Agent 独立人工审批/部署、stale/drift 检测、原子回滚和跨平台离线测试门禁。

Formal 报告生成前不能写：

> 真实 Agent 任务成功率提升 X%，成本下降 Y%。

Fake Agent 的准入/拒绝只证明控制面正确，不能作为真实模型效果。

版本化 RM-2 Candidate 只包含通用仓库规则，不含 case ID、fixture 值、期望 JSON 或 Validator 路径。标准库隐藏 Validator 在 Agent 工作区之外执行，其内容哈希进入 suite fingerprint。

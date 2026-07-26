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
| 首次 Formal 授权已中止 | 中止轮次与诊断共启动 18 次调用；没有 Formal 结果 |
| Adapter v4 smoke | 2/2 次调用完成；两臂均在 WebSocket 重试后超时并被隔离 |
| Adapter v5 HTTP-only smoke | 2/2 次调用完成；终态轨迹 2/2；重连/回退 0；基础设施排除 0 |
| Adapter v5 Formal 已中止 | 启动 32 次；一个 Forced 实验臂超时且无终态轨迹；40 次未启动；没有 Formal 结果 |
| Adapter v6 有界恢复 smoke | 2/2 次终态 turn 与稳定耗尽标记；2 个基础设施排除；readiness 失败 |
| Adapter v7 离线后续修复 | 禁止显式 cwd；单行直接 Python fallback；独立 cwd 转义错误码；真实轨迹形状已回放 |
| Adapter v7 readiness smoke | 2/2 次终态 turn；轨迹完整度 100%；基础设施排除 0；readiness 通过 |
| Adapter v7 Formal 已中止 | 启动 30 次；主机空闲睡眠使一个截止时间失效；42 次未启动；没有 Formal 结果 |
| Adapter v8 主机睡眠修复 | Windows 执行状态保护与 fail-closed 超期分类；离线验证通过 |
| Adapter v8 readiness smoke | 2/2 次终态 turn；轨迹完整度 100%；基础设施排除 0；readiness 通过 |
| Adapter v8 Formal 已中止 | 启动 13 次；一个 Forced arm 在写入后验证断言失败并耗尽 fallback；59 次未启动；没有 Formal 结果 |
| Adapter v9 写入后验证修复 | 必需写入与辅助验证分离；确定性 evaluator 保留最终判定权；105 个专项离线测试通过 |
| Adapter v9 readiness smoke | 之前失败的 application case 上 2/2 次终态 turn；轨迹完整度 100%；基础设施排除 0；readiness 通过 |
| Adapter v9 Formal 已中止 | 启动 39 次；第 39 次加载真实 PowerShell profile 后在受限 language mode 失败；fail-fast 在第 40 次前停止；部分 ScoreCard 不作为 Formal |
| Adapter v10 离线验证失效 | `codex --version` 未完整加载配置；错误的 `permissions.allow_login_shell=false` 不能作为 profile 隔离证据 |
| Adapter v10 readiness 已中止 | 第一臂在 1 秒内因配置解析失败而 fail-fast；JSONL 为空、模型请求 0、第二臂未启动；没有 readiness 或 Formal 结果 |
| Adapter v11 profile 隔离 | 改用顶层 `allow_login_shell=false`；无模型的完整配置加载验证成功，错误嵌套键对照失败 |
| Adapter v11 readiness smoke | 原失败 docs case 上 2/2 进程 exit 0；轨迹完整度 100%；基础设施排除、profile、Web Search、重连和 WebSocket 标记均为 0 |
| 已完成的 Adapter v11 Formal | 72/72 次；36 个完整配对；Baseline 25% → Forced 100%（+75pp）；中位时延 -16.7%；中位 Token -19.5%；新增回归和基础设施排除均为 0 |
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

## 第一次 Formal 已中止（不能作为 Formal 结论）

第一次获授权的执行在证据边界发现原生 Windows CWD 转义、仓库根输出约定含糊、Codex 沙箱工作目录间歇漂移以及流错误分类过宽后被主动停止。中止轮次和有界诊断共启动 18 次调用，其中 17 次形成终态进程制品，1 次在制品生成前被强制停止。该轮没有形成不可变 Formal 报告，任何中间数字都不能写入简历。

Candidate 继续冻结在 SHA-256 `bb69239c1388c5d6ec4fe44d97dc1e2f7ab13544baeeeb7d73a842c3a2a5bbcf`。新的证据边界由 suite v5、Adapter v4、Codex CLI `0.144.6`、180 秒超时、严格沙箱失败分类和恢复型重连处理共同组成。脱敏记录见 [`rm2-formal-readiness-2026-07-20.json`](evidence/rm2-formal-readiness-2026-07-20.json)。该边界随后接受了 2-call v4 smoke。

## Adapter v4 smoke 已完成（未通过就绪门禁）

评测 `evaluation-c3d92efe8cc749c48f81fa7c8dab94a8` 恰好使用两次授权调用。Baseline 与 Forced Skill 都出现 4 次 WebSocket 重试，回退 HTTPS 后在 180 秒触发超时，没有形成终态 turn；严格门禁因此得到 `quarantined (trace_incomplete)`，轨迹完整度为 0%。两臂均未出现 Windows 沙箱错误或基础设施排除，也没有完整 Token 或美元成本观测。

这证明的是传输边界失败，不能解读为“Skill 没有效果”，因为两臂都没有完成。Adapter v5 继续使用相同的 ChatGPT 认证端点，但通过受控 provider 设置 `supports_websockets=false`，直接使用 HTTP/SSE。脱敏记录见 [`rm2-v4-smoke-2026-07-21.json`](evidence/rm2-v4-smoke-2026-07-21.json)。

## Adapter v5 smoke 已完成（就绪门禁通过）

评测 `evaluation-e373ad3d6c394e88b54b67ca60523d0e` 恰好使用两次获授权的 `gpt-5.4-mini` 调用，并通过受控 HTTP/SSE provider 执行。两个进程都以 0 退出且各产生一个终态 turn；重连、WebSocket 回退、Windows 沙箱失败和基础设施排除均为 0，轨迹完整度 100%。Baseline 为 `wrong_result`，Forced Skill 通过。该轮还观测到时延 127.579s 对 99.500s、Token 65,226 对 58,284，但一个配对不足以形成 Formal 效果结论。

自动决策为 `quarantined (insufficient_valid_pairs)` 是预期行为：本次就绪 smoke 只验证传输、终态轨迹和 evaluator 链路，三项均通过；它不满足正式策略的样本数。脱敏记录见 [`rm2-v5-smoke-2026-07-22.json`](evidence/rm2-v5-smoke-2026-07-22.json)。

## Adapter v5 Formal 已中止（不能作为 Formal 结论）

随后单独获授权的 72-call Formal 在 `holdout-mobile` 第 3 次 Forced Skill 达到 180.140 秒且没有终态 turn 后按 fail-closed 停止。该 Agent 出现 5 次命令执行失败，隐藏 Validator 报告 `output_missing`。共启动 32 次调用：31 次形成进程制品，30 次成为 evaluated-run 记录，1 个完成的 Baseline 留在未闭合配对之外，1 个 Forced 调用被强制停止，40 次未启动。

这不是 v4 传输问题复发。31 个进程制品中的重连、WebSocket 回退、顶层流错误、Windows 沙箱失败和 Web Search 均为 0。但严格的 100% 轨迹门槛已无法满足，继续执行只会消耗一个必然不能成为 Formal 证据的轮次。因此该 partial attempt 没有 ScoreCard、自动决策，也不能发布成功率提升、时延、Token、回归或成本指标。脱敏记录见 [`rm2-formal-v5-attempt-2026-07-22.json`](evidence/rm2-formal-v5-attempt-2026-07-22.json)。

5 次失败的根因已经由原始 JSONL 逐条复盘：外层 PowerShell profile 与受限语言模式冲突，嵌套 PowerShell 转义破坏变量，Constrained Language 禁止方法调用，随后又检查了尚未生成的输出。Agent 后来尝试了 `cmd` 和 Python launcher，但由于没有数字化重试上限，先后进行了多轮改写和能力探测；找到可用的 `py` 时已经接近 180 秒边界，未能完成真正写入。

Adapter v6 将通用策略固定为：连续 2 次受限 PowerShell 失败后，禁止继续改写 PowerShell，只允许使用 `cmd.exe /d /s /c`、`py` 等直接可执行程序或专用非 shell 工具完成一次 fallback；fallback 不得用于无关能力探测。fallback 再失败时必须停止 shell 调用并返回 `ROOK_SHELL_FALLBACK_EXHAUSTED: <short reason>`。Normalizer v2 会记录阈值触发、恢复和耗尽诊断，并把达到阈值后的超时细分为 `codex_restricted_shell_timeout`。旧轨迹离线回放能够触发新诊断，但仍是不完整轨迹，不能转化为 Formal 结果。脱敏修复记录见 [`rm2-formal-v5-shell-remediation-2026-07-22.json`](evidence/rm2-formal-v5-shell-remediation-2026-07-22.json)。

随后单独授权的 Adapter v6 smoke 恰好执行 2 次调用。两臂都在 85 秒内产生终态 turn 并返回稳定耗尽标记，证明有界停止生效；但 readiness 仍失败。Baseline 的模型工具参数把路径中的 `\b` 编码成退格，触发 `codex_windows_sandbox_error`；Forced Skill 的直接 `py -c` fallback 把转义换行作为字面量传入，触发 `codex_shell_fallback_exhausted`。门禁为 `quarantined (excess_infrastructure_exclusions)`，有效配对为 0；这是事故与恢复行为证据，不是 Skill 效果证据。脱敏记录见 [`rm2-v6-smoke-2026-07-22.json`](evidence/rm2-v6-smoke-2026-07-22.json)。

Adapter v7 已禁止工具级 `cwd` 覆盖，要求使用正斜杠相对路径，禁止多行/字面转义换行的 `py -c` 恢复写法，并把 error 267 + 转义 cwd 细分为独立错误码；真实出现的 Constrained Language 错误也纳入分类。Candidate 与 suite 均未改变。离线回放和回归证据见 [`rm2-v6-smoke-remediation-2026-07-22.json`](evidence/rm2-v6-smoke-remediation-2026-07-22.json)。

## Adapter v7 smoke 已完成（readiness 通过）

评测 `evaluation-1611cc03d158454c8121b016f1c94f2c` 恰好使用两次获授权的 `gpt-5.4-mini` 调用。两个进程均以 0 退出并产生终态 turn，轨迹完整度 100%；基础设施排除、重连、Shell fallback 耗尽、Web Search 和 Windows 沙箱失败均为 0。Baseline 为 `wrong_result`，Forced Skill 通过。自动决策保持 `quarantined (insufficient_valid_pairs)` 是因为单配对 smoke 不能充当效果研究。脱敏 readiness 证据见 [`rm2-v7-smoke-2026-07-22.json`](evidence/rm2-v7-smoke-2026-07-22.json)。

## Adapter v7 Formal 已中止（不能作为 Formal 结论）

随后获授权的执行在 72 次计划调用中启动 30 次后由 Rook 按 fail-closed 停止。保留了 29 个进程制品和 28 个 evaluated-run 记录；42 次未启动，也没有形成 ExperimentRecord、ScoreCard、报告或 PromotionDecision。其中一个请求 180 秒的子进程记录为 18,983,156 ms：Windows 在进程活动期间因 System Idle 进入睡眠，并在恢复后把系统时间前移 18,957,278 ms。另外三次运行耗尽了有界 Shell fallback。这些基础设施失败已经使严格 Formal 门槛不可满足，因此 partial 数据不可发布，也不会复用。脱敏记录见 [`rm2-formal-v7-attempt-2026-07-22.json`](evidence/rm2-formal-v7-attempt-2026-07-22.json)。

Adapter v8 现在会为每个 Windows EvalOps 子进程持有执行状态保护；无法建立保护时在 spawn 前失败，恢复保护失败时记为清理失败，超出截止时间的 timeout 归类为 `codex_timeout_deadline_overrun`。Candidate、Normalizer 和 sealed suite 未改变。离线修复证据见 [`rm2-formal-v7-host-sleep-remediation-2026-07-22.json`](evidence/rm2-formal-v7-host-sleep-remediation-2026-07-22.json)。随后单独获授权的 v8 smoke 恰好完成 2 次调用：终态轨迹 2/2、轨迹完整度 100%、基础设施排除 0，也没有 timeout overrun；Baseline 为 `wrong_result`，Forced Skill 通过。之后获授权的全新 72-call Formal 在启动 13 次后按 fail-closed 停止。12 次形成完整终态制品，1 次在途调用在制品前停止，59 次未启动。失败的 Forced arm 已写入目标，但辅助源文件归一化断言失败并返回 `ROOK_SHELL_FALLBACK_EXHAUSTED`；Adapter v8 在确定性 evaluator 运行前将其归类为 Adapter 错误，零排除合同已不可满足。没有 Formal ScoreCard 或简历指标。脱敏记录见 [`rm2-formal-v8-attempt-2026-07-22.json`](evidence/rm2-formal-v8-attempt-2026-07-22.json)。

Adapter v9 与 Normalizer v3 已在离线环境中区分两类结果：fallback 未完成必需写入时仍作为 Adapter 错误 fail closed；目标已经写入、只有辅助检查不确定时，记录 `codex_post_write_verification_inconclusive` 并交给确定性 evaluator 判定工作区结果。Prompt v15 同时禁止在同一 fallback 命令中捆绑写入与辅助断言。该修复不是 live readiness 或 Formal 证据；记录见 [`rm2-formal-v8-post-write-remediation-2026-07-22.json`](evidence/rm2-formal-v8-post-write-remediation-2026-07-22.json)。

随后单独授权的 v9 readiness smoke 在之前失败的 application case 上恰好完成 2 次调用。两臂都产生终态轨迹并执行确定性 evaluator，轨迹完整度 100%，基础设施排除 0；Baseline 错误，Forced Skill 通过。单配对自动结论 `quarantined (insufficient_valid_pairs)` 符合 readiness 设计，不能作为 Formal 效果估计。脱敏记录见 [`rm2-v9-smoke-2026-07-24.json`](evidence/rm2-v9-smoke-2026-07-24.json)。

## 已完成的 Adapter v11 Formal

单独授权的 sealed holdout 使用 `gpt-5.4-mini` 完成 72/72 次调用和全部 36 个
Baseline/Forced 配对。Baseline 通过 9/36（25%，Wilson 95% 区间
13.8%–41.1%），Forced Skill 通过 36/36（100%，Wilson 95% 区间
90.4%–100%），配对提升 75 个百分点。中位时延从 69.773s 降至
58.141s（-16.7%），完整观测的中位 Token 从 42,436 降至
34,174（-19.5%），中位工具调用从 6 降至 4（-33.3%）。能力任务由
0/18 提升为 18/18，18 个 preservation 配对全部通过，新增回归为 0。

72 个进程全部 exit 0 且各有一个终态 turn，轨迹完整度 100%；基础设施排除、
profile、Web Search、重连、WebSocket、Windows 沙箱、安全失败、秘密泄漏和
隔离泄漏均为 0。自动门禁为 `promoted (capability_success_uplift)`，但
measurement-only 执行没有产生人工审批或部署。美元成本和 Codex 路由仍未观测。
脱敏证据见
[`rm2-formal-v11-summary-2026-07-26.json`](evidence/rm2-formal-v11-summary-2026-07-26.json)。

### Formal 真实评测填写合同

以下字段不能用估算值替代。只有在显式授权外部调用和费用，并生成不可变报告后才能填写。

| 指标 | 必需证据 | 当前值 |
| --- | --- | --- |
| 能力配对样本数 | 排除基础设施失败后的 Direct/Transfer 配对 | 18 |
| Baseline 成功率 | Baseline passed / 有效 Baseline | 总体 25%；能力任务 0% |
| Forced Skill 成功率 | Forced passed / 有效 Forced | 总体和能力任务均为 100% |
| 配对成功率提升 | Forced-Baseline 的配对均值，并附任务分层 bootstrap 95% 区间 | 总体 +75pp；能力任务 +100pp（95% bootstrap 区间 +100pp 到 +100pp） |
| 新增回归 | Baseline 通过但 Candidate 失败的 Regression/Adversarial 案例 | 18 个 preservation 配对中为 0 |
| 中位时延变化 | 配对毫秒中位数 | 69.773s → 58.141s（-16.7%） |
| Token 变化 | 可观测输入/输出 Token 配对值 | 42,436 → 34,174（-19.5%） |
| 成本变化 | 可观测模型费用配对值 | 未观测 |
| 路由 precision/recall | 只能来自可靠的 `skill_loaded` 身份事件 | Codex 未观测 |

真实协议分为 12 次 Calibration（`calibration.toml`）、24 次 Pilot（`pilot.toml`）和 72 次 Formal（sealed 且与 Pilot 不重叠的 `suite.toml`，12 案例 x 3 次重复 x 2 个实验臂）。Formal manifest 锁定 Candidate content hash；一旦变化，会在 Agent 调用前失败。每一阶段都需要单独显式授权，并在进入下一阶段前暂停。发布任何指标时，必须同时记录 suite fingerprint、policy fingerprint、目标模型版本、重复次数、基础设施排除项、不可变报告路径和授权状态。

正式执行使用 `rook eval run --model <model>` 显式指定 Codex 模型；可选 live smoke 使用 `ROOK_CODEX_EVAL_MODEL`。模型会进入目标指纹，不能依赖被隔离执行忽略的用户配置。

## 简历表述边界

现在可以写：

> 设计并实现 Rook Forge Skill 治理控制面，支持隔离配对实验、确定性评测、ScoreCard、quarantine、自动门禁、按 Agent 独立人工审批/部署、stale/drift 检测、原子回滚和跨平台离线测试门禁。

附带 Formal 证据后还可以写：

> 在 sealed 的 72-call `gpt-5.4-mini` holdout 上，将配对任务成功率从 25%
> 提升到 100%（+75pp），中位时延降低 16.7%、完整观测 Token 降低 19.5%，
> 新增回归和基础设施排除均为 0。

仍然不能写“美元成本下降”或“Codex 路由 precision/recall 提升”，因为这些
字段没有被观测。

Fake Agent 的准入/拒绝只证明控制面正确，不能作为真实模型效果。

版本化 RM-2 Candidate 只包含通用仓库规则，不含 case ID、fixture 值、期望 JSON 或 Validator 路径。标准库隐藏 Validator 在 Agent 工作区之外执行，其内容哈希进入 suite fingerprint。

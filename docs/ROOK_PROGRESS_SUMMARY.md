# Rook 项目进度摘要

更新时间：2026-07-26

## 项目定位

Rook 是一个可真实运行的本地 Python Coding Agent；Rook Forge 是内置的 Skill 考试、上线审批、部署与版本回滚控制面。Forge 通过隔离执行、基线对照、工具轨迹和安全回归判断 Candidate 是否具备上线资格，但自动门禁不会直接激活 Skill，必须经过按目标独立、不可变的人工审批。

第一版范围已经收敛为：

- Rook 自身作为进程内执行与部署目标；
- Codex CLI 作为唯一外部评测和仓库级部署目标；
- Claude Code 集成暂缓，后续通过现有 Adapter 扩展接口接入。

## 当前开发位置

- 分支：`main`
- 工作树：`D:/WorkAndStudy/FindJob/New-Harness-Agent/Rook`
- Rook Forge v0.2.2 已发布；Adapter v9 suite 基线为 `94e866a`，作品集与证据 PR #9 合并提交为 `8e56e14`。
- 当前状态：Adapter v11 已完成全新的 72-call Formal：72/72 次调用、36 个完整配对、轨迹完整度 100%、基础设施排除 0。Baseline 成功率 25%，Forced Skill 100%（+75pp）；中位时延降低 16.7%，中位 Token 降低 19.5%，新增回归 0。自动门禁为 `promoted (capability_success_uplift)`；measurement-only 执行未产生人工审批或部署，美元成本和 Codex 路由仍未观测。

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
36. 发布 v0.2.2，完成全新虚拟环境 wheel 安装、CLI 帮助与 `rook eval demo` 验证。
37. Adapter v9 readiness 恰好完成 2/2 次真实调用，之前失败的写入后 application case 由隐藏确定性 evaluator 正确判定。
38. 新增 GitHub Actions CI guard 与 RAG evidence reporter 两个不同类型 Skill，使用两个公开仓库的固定 commit/blob 构建四个 Direct/Regression/Adversarial holdout。
39. 本地治理 dogfood 实际生成四个审批、四个部署、一次 Codex 漂移检测/恢复和两个事务回滚；README 首页压缩为问题—架构—演示—指标，并发布技术文章和 150 秒演示视频。

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

- 当前质量门禁：`492 passed, 5 skipped`，覆盖率 `85.12%`。
- 当前远端完整核心离线基线：Ubuntu Python 3.11/3.12 均为 `1753 passed, 7 skipped`；Windows Python 3.11/3.12 均为 `1754 passed, 6 skipped`。默认外部评测关闭，不会启动真实 Codex 或产生模型费用。
- Ruff 全仓关键规则、mypy 核心 EvalOps 边界和 pip-audit 均通过；pip-audit 未发现已知第三方依赖漏洞，本地未发布包按预期标记为不可从 PyPI 审计。
- `rook-agent 0.2.2` wheel/sdist 已实际构建；wheel 在全新临时虚拟环境中完成安装、版本导入、`rook --help` 和 `rook eval demo`，双目标部署/替换/漂移检测/回滚全链路通过。
- v0.2.2 wheel SHA-256 为 `4aa5cf99301a5a96cd656dfd228569f0cb471d3e8457cd9fd630f46cc66fdf19`；sdist SHA-256 为 `c01a5bc176d3f876ddf2aa39265e25517b0a46b1922a22833265bebaef04c7f3`。
- GitHub Actions [run 30081525920](https://github.com/ZHUMUJUN/Rook/actions/runs/30081525920) 全绿：Ubuntu Python 3.11/3.12 各 `1753 passed, 7 skipped`，Windows Python 3.11/3.12 各 `1754 passed, 6 skipped`；Quality 为 `492 passed, 5 skipped`、85.12% 覆盖率，Ruff、mypy 与 pip-audit 均通过。
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
- 第一次获授权的 Formal 在证据协议发现 Windows CWD 转义、沙箱工作目录漂移、输出路径歧义和恢复型流事件误分类后被中止；连同有界诊断共启动 18 次调用，其中 17 次形成终态进程制品、1 次在制品前强制停止，没有产生 Formal 报告或简历指标。
- 获授权的 2-call Adapter v4 smoke 已完整调度，但 Baseline 与 Forced Skill 均经历 4 次 WebSocket 重试、回退 HTTPS 后在 180 秒超时；门禁为 `quarantined (trace_incomplete)`，轨迹完整度 0%，没有 Windows 沙箱错误，也不能作为 Skill 效果结论。
- Candidate hash 继续冻结为 `bb69239c...bbcf`；Adapter v5 使用同一 ChatGPT 认证端点并设置 `supports_websockets=false`，Adapter/Normalizer/CLI/RM-2/Policy/ScoreCard 专项 `134 passed`，Ruff 与 mypy 已通过。
- 获授权的 2-call Adapter v5 HTTP-only smoke `evaluation-e373ad3d6c394e88b54b67ca60523d0e` 恰好完成 2/2 次调用：两臂进程均 exit 0、各有一个 `turn.completed`，重连/回退/Windows 沙箱失败/基础设施排除均为 0，轨迹完整度 100%；Baseline 为 `wrong_result`，Forced Skill 为 `passed`。
- 本次 smoke 只有一个配对，因此自动门禁按样本阈值保持 `quarantined (insufficient_valid_pairs)`；它证明 Adapter v5 已具备 Formal 就绪条件，不是简历效果结论。美元成本仍未观测。
- 随后获授权的 72-call Formal `exp-5362363eba0b425b96efa6500ba6c22e` 在 `holdout-mobile` repetition 3 Forced Skill 达到 180.140 秒且没有 `turn.completed` 后停止：32 次已启动、31 个进程制品、30 个 evaluated-run 记录、40 次未启动。
- 31 个进程制品中 HTTP-only provider mismatch、重连、WebSocket 回退、顶层流错误、Windows 沙箱失败和 Web Search 均为 0；失效点是 Agent 在受限 PowerShell 下连续命令失败并最终 `output_missing`，不是传输回归。
- 严格的 100% 轨迹门槛已不可满足，因此没有生成 ScoreCard、自动门禁或可写简历的 Formal 成功率/时延/Token/回归指标；美元成本仍未观测。Partial 数据不会与未来重跑拼接。
- 原始失效轨迹包含 10 次命令执行、5 次失败；前 2 次受限 PowerShell 失败后仍继续改写命令和探测 launcher，直到接近 180 秒边界才发现可用的 `py`。离线回放确认最大连续受限失败为 4，并保留 `turn_terminal_missing`。
- Adapter v6 将阈值固定为连续 2 次，只允许一次 `cmd.exe` / 直接可执行程序 / 专用非 shell fallback；再失败时返回稳定的 `ROOK_SHELL_FALLBACK_EXHAUSTED`。Normalizer v2 新增阈值、恢复、耗尽诊断和 `codex_restricted_shell_timeout`。
- 获授权的 Adapter v6 smoke `evaluation-0b193738a0bc4b8cbbd5fbd8807b55b9` 恰好执行 2/2 次调用：两臂均 exit 0、各有一个 `turn.completed` 和稳定耗尽标记，分别在 84.641s、70.906s 结束，没有复现 180 秒静默超时。
- 本次 smoke 仍为 `quarantined (excess_infrastructure_exclusions)`：Baseline 因模型工具参数中的 `\b` 路径转义触发 `codex_windows_sandbox_error`，Forced Skill 的 `py -c` fallback 因字面转义换行触发 `codex_shell_fallback_exhausted`；有效配对 0，不能产生 Skill 效果或 Formal 指标。
- Adapter v7 禁止模型覆盖 EvalOps 工具 `cwd`，要求正斜杠相对路径，禁止多行或字面转义换行的 `py -c`；新增 `codex_windows_tool_cwd_escape_error`，并识别真实出现的 `Cannot create type` Constrained Language 错误。真实 v6 轨迹形状已加入脱敏回放测试。
- 获授权的 Adapter v7 smoke `evaluation-1611cc03d158454c8121b016f1c94f2c` 恰好执行 2/2 次调用：两臂均 exit 0 并有终态 turn，轨迹完整度 100%，基础设施排除、重连、Shell fallback 耗尽、Web Search 和 Windows 沙箱失败均为 0；Baseline 为 `wrong_result`，Forced Skill 为 `passed`。
- 随后获授权的 Formal `exp-1e1c359c31ea4cdc886c287767749352` 启动 30/72 次，保留 29 个进程制品和 28 个 evaluated-run 记录，42 次未启动；没有 ExperimentRecord、ScoreCard、报告、门禁或简历指标，partial 数据不会与未来执行合并。
- 一次 180 秒进程记录为 18,983,156ms。Windows System Event 证明主机因 `System Idle` 睡眠，并在恢复后把系统时间前移 18,957,278ms；该时间增量与进程超期基本一致。另有 3 次 `codex_shell_fallback_exhausted`，严格 Formal 门槛已不可满足，因此进程树被停止且后代进程验证为 0。
- Adapter v8 为 Windows EvalOps 子进程持有 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`；保护建立失败在 spawn 前 fail closed，保护恢复失败进入 cleanup error，超过 deadline 5 秒的 timeout 映射为 `codex_timeout_deadline_overrun` 基础设施错误。
- Adapter v8 当前直接专项 `102 passed, 1 skipped`，扩展 EvalOps/上下文专项 `494 passed, 7 skipped`；Ruff、核心 EvalOps mypy、证据 JSON 校验和 `git diff --check` 均通过。
- 提交 `541b203` 已推送；GitHub Actions run `29884679303` 的质量门禁、Ubuntu 3.11/3.12 与 Windows 3.11/3.12 共 5/5 job 全绿。质量门禁为 `482 passed, 5 skipped`、覆盖率 85.09%；Ubuntu 为 `1743 passed, 7 skipped`，Windows 为 `1744 passed, 6 skipped`。
- 获授权的 Adapter v8 smoke `evaluation-887d35ad04174b67a61b8ae1355ebb98` 恰好执行 2/2 次调用：两臂 exit 0 且各有一个终态 turn，轨迹完整度 100%，基础设施排除、重连、WebSocket、Web Search、Shell fallback 耗尽、Windows 沙箱失败和 deadline overrun 均为 0；Baseline 为 `wrong_result`，Forced Skill 为 `passed`。
- v8 smoke 的自动门禁为 `quarantined (insufficient_valid_pairs)`，仅因为一个配对低于效果策略样本阈值；readiness 已通过，但该轮不是 Formal 指标，美元成本仍未观测。
- 随后获授权的 v8 Formal `exp-e01d6b5eea5440d1963eaf02b5bd803e` 启动 13/72 次：12 次形成完整终态进程制品和 evaluated-run 记录，1 次在途调用在制品前停止，59 次未启动；所有已完成进程均 exit 0、清理成功、轨迹完整，重连、Web Search、Windows 沙箱失败和 deadline overrun 均为 0。
- `holdout-application` repetition 3 Forced arm 已通过直接 Python fallback 写入 `release.json`，但同一进程中的辅助源文件归一化断言失败，Agent 返回稳定的 `ROOK_SHELL_FALLBACK_EXHAUSTED`；Adapter v8 因此记为 `adapter_error`，确定性 evaluator 未执行。严格零排除 Formal 合同已不可满足。
- 该轮没有 ExperimentRecord、ScoreCard、PromotionDecision 或可写简历的 Formal 成功率/时延/Token 指标；partial 数据不会复用，美元成本仍未观测。
- Adapter v9 将 fallback 必需写入与辅助验证分离；真正写入失败仍返回 `ROOK_SHELL_FALLBACK_EXHAUSTED` 并 fail closed，已写入但辅助验证不确定时返回 `ROOK_POST_WRITE_VERIFICATION_INCONCLUSIVE`，由隐藏确定性 evaluator 决定正确性。Normalizer v3 保留审计诊断但不制造基础设施排除，Prompt v15 禁止在一个 fallback 命令中捆绑写入和断言；专项离线测试 `105 passed`。
- 获授权的 Adapter v9 readiness `evaluation-de3f7652fa0447e193bd1ddda8b51ce9` 恰好执行 2/2 次调用：两臂 exit 0 且各有一个终态 turn，轨迹完整度 100%，基础设施排除、Web Search、重连、顶层流错误、Windows 沙箱失败、fallback 耗尽和写入后不确定标记均为 0；Baseline 为 `wrong_result`，Forced Skill 为 `passed`。
- v9 readiness 的自动门禁为 `quarantined (insufficient_valid_pairs)`，仅因为一个配对低于效果政策样本阈值。该轮证明当前 Adapter 就绪，不产生 Formal 成功率结论。
- Adapter v9 Formal 在 39/72 次时因真实 PowerShell profile 在受限 language mode 中加载而 fail-fast；Adapter v10 的首次配置隔离使用了错误的嵌套键，readiness 在模型请求前停止。Adapter v11 改用顶层 `allow_login_shell=false`，并让 `rook eval doctor` 通过无模型的 `features list` 完整校验所有 EvalOps 配置。
- Adapter v11 readiness 在原失败 `holdout-docs` 边界完成 2/2：两个进程 exit 0、终态 turn 2/2、轨迹完整度 100%，profile、Web Search、重连、WebSocket、沙箱失败和基础设施排除均为 0。
- 随后单独授权的 Adapter v11 Formal `evaluation-3234f8305aaf4ec7818837ca1a016ac3` 从零完成 72/72 次调用和 36 个配对。72 个进程全部 exit 0、终态 turn 72/72、轨迹完整度 100%；基础设施排除、profile、Web Search、重连、WebSocket、Windows 沙箱失败、安全失败、秘密泄漏和隔离泄漏均为 0。
- Formal 总体 Baseline 为 9/36（25%，Wilson 95% 区间 13.8%–41.1%），Forced Skill 为 36/36（100%，Wilson 95% 区间 90.4%–100%），配对提升 +75pp；18 个能力配对由 0/18 提升到 18/18，18 个 preservation 配对新增回归为 0。
- Formal 中位时延由 69.773s 降至 58.141s（-16.7%），中位 Token 由 42,436 降至 34,174（-19.5%），中位工具调用由 6 降至 4（-33.3%）。美元成本和 Codex 路由 precision/recall 未观测，不做估算。
- Formal 自动门禁为 `promoted (capability_success_uplift)`；本轮使用 measurement-only，因此没有人工审批、部署或活动版本副作用。脱敏证据为 `docs/evidence/rm2-formal-v11-summary-2026-07-26.json`。
- Formal 证据同步后的完整 EvalOps 回归为 `503 passed, 7 skipped`，覆盖率 `86.08%`；Ruff、mypy、证据 JSON 与 `git diff --check` 全部通过。
- 两个真实仓库 holdout 的 4 个 Candidate/suite/provenance/hidden-validator 专项测试通过；它们仍为 staged/quarantined，没有 live model gate 或部署。
- 本地治理 dogfood 生成 4 个不可变 ApprovalRecord、6 个 ReleaseRecord（4 次部署 + 2 次回滚），在 Codex `SKILL.md` 被手工修改后报告 `drifted`，精确恢复后重新变为 `active`，最终 Rook/Codex 都回滚到 v1。考试使用 Fake Agent，因此只证明控制面和文件事务。

## 下一阶段计划

1. 将 Adapter v11 Formal 证据提交、推送并由 Windows/Linux CI 验证。
2. 如需真实上线演示，使用非 measurement-only 的治理流程重新登记门禁决定，再显式人工 approve；不得直接把本次报告当作部署授权。
3. 对两个真实仓库 holdout 分别申请 live Calibration/Pilot，验证跨 Skill/跨仓库泛化。
4. 可选安装 `evalplus` 并运行独立 benchmark gate；它不阻塞 Codex-only MVP。

## 当前停点

Rook Forge 产品闭环已经形成，并可由 `rook eval demo` 零配置复现：Candidate → 隔离考试 → ScoreCard → 自动门禁 → 人工审批 → Rook/Codex 独立部署 → stale/drift 检测 → 原子回滚。自动门禁通过后保持 inactive，只有 approve 才会进入运行时或仓库级 Codex Skill 目录。

手工与自动 Candidate 共用同一条治理链路，自动生成结果保持 quarantined，当前没有旁路准入机制。历史 partial Formal 都被证据边界正确阻断；最终 Adapter v11 Formal 从零完整结束，成功率、Token 和时延指标现在具备可复核的简历证据。美元成本和 Codex 路由仍保持 `not observed`。自动门禁通过不等于上线，本次 measurement-only 报告没有产生人工审批或部署副作用。

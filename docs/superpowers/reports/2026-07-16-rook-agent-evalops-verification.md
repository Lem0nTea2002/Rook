# Rook Codex-only EvalOps Verification Report

- 验证日期：2026-07-16
- 分支：`feature/rook-forge`
- 功能提交：`bac7ec1 feat: quarantine trace-derived Skill candidates`
- Windows 硬化提交：`116b04f fix: preserve CandidateStore publish conflicts`
- 核心维护提交：`5a94aac fix: stabilize core tests on Windows`、`5331a9a fix: run ChainSWE verification portably`
- 离线 CI 提交：`a94a531 ci: test offline suite on Windows and Linux`
- 人工 Candidate 提交：`fba8b68 feat: stage manual Skill candidates safely`
- 简历证据套件提交：`b085dea test: add portfolio EvalOps evidence suite`

## 结论

Codex-only EvalOps 的手工 Candidate 和轨迹生成 Candidate 已共用同一条显式评测、准入、登记、报告与回滚链路。自动生成结果仅以 `quarantined` 状态进入 `.rook/skill-registry`，不会直接写入可发现 Skill 目录、设置活动版本或导出。

EvalOps/evolution 专项和完整核心基线均为零失败；默认验证没有启动真实 Codex 任务或产生模型费用。实施前记录的 27 个核心失败以及首次 Task 16 审计剩余的 11 个稳定失败均已在独立维护提交中关闭。

## 验证结果

| 范围 | 结果 | 说明 |
|---|---:|---|
| EvalOps + evolution 全集 | 585 passed, 7 skipped | 592 个用例；零失败 |
| Task 14 与直接依赖 | 323 passed | 严格蒸馏、Gate、隔离存储、幂等协调、Runtime/Factory/TUI 接入 |
| Windows 与安全硬化专项 | 295 passed, 3 skipped | 原子发布、进程取消、隐藏 evaluator、路径逃逸、脱敏、回滚 |
| 历史维护直接回归 | 131 passed | AgentLoop/App/patch/ChainSWE/runtime 专项，零失败 |
| 完整核心基线（排除 EvalPlus） | 1596 passed, 10 skipped | 总计 1606；零失败；外部评测与费用开关均为 0 |
| 简历证据控制实验 | 3 passed | 12-case suite 结构、文档声明边界、有效/中性/危险 Candidate 决策 |
| 可选 EvalPlus gate | collection error | 缺少可选依赖 `evalplus`，与实施前记录一致 |
| 真实 Codex smoke | skipped | `ROOK_RUN_EXTERNAL_EVALS=0`、`ROOK_ALLOW_MODEL_COSTS=0`，未授权外部调用 |
| Fake Agent demo | 1 passed | 完成 Candidate → A/B → ScoreCard → Decision → Registry → Rollback |

完整核心基线的实施前记录为 `956 passed, 27 failed, 3 skipped`，同样排除 `tests/test_evalplus_benchmark.py`。当前完整核心基线已经全绿。

## 历史失败关闭审计

首次 Task 16 审计中的问题已由两个独立维护提交关闭：

- `5a94aac`：并发测试改用线程 barrier 证明重叠执行，不再依赖 350ms 墙钟阈值；app factory 断言同步到 runtime 预分类设计；`collect_git_diff` 要求传入路径本身是 Git 顶层，避免继承父级仓库。
- `5331a9a`：ChainSWE verifier 直接以 `sh -c` 执行，并在 Windows 从 Git for Windows 定位 `sh.exe`；外部已排序的 ChainSWE issue 使用注入式本地边界决策，不再产生隐藏 Provider 分类调用。

此前单独复跑通过的 mutation tool 文件系统波动在最终完整核心运行中也已通过。最终命令及结果：

```text
ROOK_RUN_EXTERNAL_EVALS=0
ROOK_ALLOW_MODEL_COSTS=0
python -m pytest -q --ignore=tests/test_evalplus_benchmark.py
1596 passed, 10 skipped in 112.61s
```

全量运行中曾暴露 CandidateStore 临时目录清理的 `WinError 32`，该错误会覆盖正确的并发 `FileExistsError`。`116b04f` 增加了 Windows 有界清理重试，并在清理仍失败时保留原始发布异常；最终全量运行不再出现该失败，专项连续复跑通过。

`.github/workflows/offline-tests.yml` 已配置 Python 3.11 的 `ubuntu-latest` / `windows-latest` 矩阵，显式将两个外部调用开关设为 `0`，并运行同一完整核心命令。该 workflow 需在分支推送后完成首次远端执行；本报告不把本地验证冒充为 GitHub Actions 成功。

## 外部调用与本机能力

默认 live smoke 结果：

```text
SKIPPED: set ROOK_RUN_EXTERNAL_EVALS=1 to enable live Codex smoke tests
```

本轮完整回归显式设置 `ROOK_RUN_EXTERNAL_EVALS=0` 和 `ROOK_ALLOW_MODEL_COSTS=0`，因此没有提交真实 Codex 任务。此前无模型调用的 `rook eval doctor` 结果：

- Rook：available，in-process，structured events，isolation；
- Codex：available，`D:\Develop\NodeJs\codex.EXE`，`codex-cli 0.144.1`，structured events，isolation；
- doctor 不检查认证，也不产生模型调用。

worktree 的 `.venv` 已重新执行 `pip install --no-deps -e .`，确认 `rook eval doctor` 使用当前分支 CLI，而不是旧 console-script 安装。

## Demo 证据

Fake Agent 演示在隔离 pytest 根目录生成两个不可变报告：

- `evaluation-6e6d6d29de5943d5bc34cd1fbf515700/report.md`
- `evaluation-bfade672d84c4e4aad4f698092b8cad3/report.md`

本轮可复现路径位于 `.pytest_cache/task16-demo/.../.rook/evalops/artifacts/reports/`。两个 Rook 内容版本均得到 `promoted` 内容决策；Codex 当前没有可靠 Skill 激活事件，因此路由结论保持 `quarantined/not observed`；Registry 最后原子 rollback 到版本 1。真实 Codex 未运行，所以不报告虚构的 live 决策或提升。

新增的 `evals/suites/release-manifest` 提供 Direct、Transfer、Regression、Adversarial 各 3 个版本化案例；`evals/candidates/release-manifest` 提供有效、中性、危险三个同名版本。`tests/test_evalops_portfolio.py` 使用 Fake Agent 验证有效版本准入、中性版本无提升拒绝、危险版本 adversarial 拒绝，并检查中英文证据文档没有把这些控制结果写成真实模型提升。人工版本可通过 `rook skill stage --bundle ...` 以 `imported/quarantined` 状态离线入库。

## 安全与隔离证据

- CandidateStore：版本不可变、no-replace 发布、Windows `WinError 5/32` 有界重试、竞争失败不覆盖。
- Evaluator：command 无 shell、超时清理进程树、隐藏脚本受 suite 根约束；file_state 拒绝绝对路径、遍历和逃逸。
- 制品：原始事件在归一化前脱敏，JSON/JSONL 原子落盘，敏感 key 和 credential-shaped 值不持久化。
- LLM Judge/Distiller：默认关闭或 opt-in；`tool_choice="none"`、低温、token 上限、严格 JSON schema、一次格式重试、Provider 故障不伪装为 Skill 失败。
- 自动 Candidate：每个 procedure/verification/pitfall 都绑定当前 trace 的 `event_id:part_id`；Gate 拒绝秘密、波动内容、注入和未落地执行声明。
- Registry：不可变历史、target 独立活动指针、stale 检测、原子 rollback；promotion/export 不写真实 `~/.codex`。

Windows 非管理员环境无法创建符号链接，因此 4 个 symlink 逃逸用例跳过；对应路径规范化和 containment 的非 symlink 用例已通过。另有 POSIX dirfd/process-group 两个跨平台专项在 Windows 跳过。

## 设计验收映射

| 设计 §19 | 验证证据 |
|---|---|
| Rook/Codex 隔离实验 | adapter contract、Rook/Codex adapter、runner/service 测试 |
| 同 snapshot 的 Baseline/Forced | workspace 与 runner 配对测试 |
| 内容/路由分离 | scoring 与 policy 测试；Codex 路由保持未观测 |
| 四类案例 | `tests/test_evalops_demo.py` 与 `evals/suites/codex-demo` |
| 外部 evaluator | evaluator、suite loader、runner 测试 |
| 原始事件与统一轨迹 | Rook/Codex normalizer 与 artifact 测试 |
| 基础设施状态独立 | runner/scoring 测试 |
| ScoreCard 完整性 | scoring/report golden tests |
| 安全/回归阻断 promotion | policy Fast/Full Gate 测试 |
| 按 Agent 独立决策 | service 与 registry 测试 |
| stale | registry fingerprint 测试 |
| 不修改真实 Codex 配置 | materializer、CLI export 和 adapter 隔离测试 |
| 原子 rollback | registry 与 demo 测试 |
| 默认 Fake/no cost | 585/7 专项、1596/10 完整核心与 live skip |
| 可选本机 Codex smoke | doctor 已探测可用；真实付费路径因未授权未执行 |

## 剩余限制与非声明

- Codex 路由 precision/recall 在缺少可靠激活事件时继续为 `None/not observed`。
- LLM Judge 不是默认成功依据，也不能覆盖确定性失败。
- 未安装可选 `evalplus` 包。
- 未获得费用授权，未声明真实 Codex 的成功率、成本、时延或内容提升。
- 新增双平台 workflow 尚待推送后的首次 GitHub Actions 执行。
- 未实现 Claude Code 集成，不修改模型权重，也不宣称通用自主进化。

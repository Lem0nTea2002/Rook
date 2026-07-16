# Rook Codex-only EvalOps Verification Report

验证日期：2026-07-16  
分支：`feature/rook-forge`  
功能提交：`bac7ec1 feat: quarantine trace-derived Skill candidates`  
Windows 硬化提交：`116b04f fix: preserve CandidateStore publish conflicts`

## 结论

Codex-only EvalOps 的手工 Candidate 和轨迹生成 Candidate 已共用同一条显式评测、准入、登记、报告与回滚链路。自动生成结果仅以 `quarantined` 状态进入 `.rook/skill-registry`，不会直接写入可发现 Skill 目录、设置活动版本或导出。

EvalOps/evolution 专项零失败；默认验证没有启动真实 Codex 任务或产生模型费用。全量核心基线的失败数由实施前记录的 27 个降至本轮 14 个；其中 3 个负载型失败单独复跑通过，稳定剩余 11 个均属于本轮未修改的历史模块或旧断言。

## 验证结果

| 范围 | 结果 | 说明 |
|---|---:|---|
| EvalOps + evolution 全集 | 574 passed, 7 skipped | 581 个用例；零失败 |
| Task 14 与直接依赖 | 323 passed | 严格蒸馏、Gate、隔离存储、幂等协调、Runtime/Factory/TUI 接入 |
| Windows 与安全硬化专项 | 295 passed, 3 skipped | 原子发布、进程取消、隐藏 evaluator、路径逃逸、脱敏、回滚 |
| 直接影响回归 | 211 passed, 2 failed | 两个稳定失败均为既有问题，见下文 |
| 完整核心基线（排除 EvalPlus） | 1569 passed, 14 failed, 10 skipped | 总计 1593；3 个失败复跑通过 |
| 可选 EvalPlus gate | collection error | 缺少可选依赖 `evalplus`，与实施前记录一致 |
| 真实 Codex smoke | 1 skipped, 2 deselected | 未设置 `ROOK_RUN_EXTERNAL_EVALS=1`，未授权费用 |
| Fake Agent demo | 1 passed | 完成 Candidate → A/B → ScoreCard → Decision → Registry → Rollback |

完整核心基线的历史记录为 `956 passed, 27 failed, 3 skipped`，同样排除 `tests/test_evalplus_benchmark.py`。本轮没有 EvalOps/evolution 失败，也没有新增的稳定失败归因于本分支实现。

## 全量失败审计

以下 3 个全量运行失败在隔离复跑时通过，判定为机器负载相关时序/文件系统波动：

- `tests.test_agent_context_loop::test_agent_loop_runs_readonly_tool_calls_in_parallel_and_appends_results_in_order`
- `tests.test_agent_context_loop::test_agent_loop_streaming_runs_readonly_tool_calls_in_parallel`
- `tests.test_mutation_tools::test_edit_can_replace_all_matches_when_enabled`

以下 11 个为稳定或既有核心失败：

- `tests.test_agent_context_loop::test_agent_loop_runs_bypass_allowed_tool_calls_in_parallel`：Windows 下 200ms 并行用例偶尔超过固定 350ms 阈值。
- `tests.test_app_factory::test_create_rook_app_exposes_task_boundary_in_real_prompt`：测试仍期待“模型每轮调用 task_boundary”的旧文案，当前生产提示明确由 runtime 预分类。
- `tests.test_eval_patch::test_collect_git_diff_returns_empty_for_non_git_directory`。
- `tests.test_chainswe_runner` 下 8 个既有 ChainSWE runner 用例。

从范围基线 `697fc33` 到当前，`AgentLoop`、ChainSWE、eval patch、mutation tool 和 system prompt 的失败所属生产文件均未修改；`test_app_factory.py` 只增加 CandidateCoordinator 接线测试，失败断言和对应 system prompt 也未修改。按计划未把这些历史核心问题混入 EvalOps 提交。

全量运行中曾暴露 CandidateStore 临时目录清理的 `WinError 32`，该错误会覆盖正确的并发 `FileExistsError`。`116b04f` 增加了 Windows 有界清理重试，并在清理仍失败时保留原始发布异常；最终全量运行不再出现该失败，专项连续复跑通过。

## 外部调用与本机能力

默认 live smoke 结果：

```text
SKIPPED: set ROOK_RUN_EXTERNAL_EVALS=1 to enable live Codex smoke tests
```

未设置 `ROOK_RUN_EXTERNAL_EVALS` 或 `ROOK_ALLOW_MODEL_COSTS`，因此没有提交真实 Codex 任务。无模型调用的 `rook eval doctor` 结果：

- Rook：available，in-process，structured events，isolation；
- Codex：available，`D:\Develop\NodeJs\codex.EXE`，`codex-cli 0.144.1`，structured events，isolation；
- doctor 不检查认证，也不产生模型调用。

worktree 的 `.venv` 已重新执行 `pip install --no-deps -e .`，确认 `rook eval doctor` 使用当前分支 CLI，而不是旧 console-script 安装。

## Demo 证据

Fake Agent 演示在隔离 pytest 根目录生成两个不可变报告：

- `evaluation-6e6d6d29de5943d5bc34cd1fbf515700/report.md`
- `evaluation-bfade672d84c4e4aad4f698092b8cad3/report.md`

本轮可复现路径位于 `.pytest_cache/task16-demo/.../.rook/evalops/artifacts/reports/`。两个 Rook 内容版本均得到 `promoted` 内容决策；Codex 当前没有可靠 Skill 激活事件，因此路由结论保持 `quarantined/not observed`；Registry 最后原子 rollback 到版本 1。真实 Codex 未运行，所以不报告虚构的 live 决策或提升。

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
| 默认 Fake/no cost | 574/7 专项与 live skip |
| 可选本机 Codex smoke | doctor 已探测可用；真实付费路径因未授权未执行 |

## 剩余限制与非声明

- Codex 路由 precision/recall 在缺少可靠激活事件时继续为 `None/not observed`。
- LLM Judge 不是默认成功依据，也不能覆盖确定性失败。
- 未安装可选 `evalplus` 包。
- 未获得费用授权，未声明真实 Codex 的成功率、成本、时延或内容提升。
- 未实现 Claude Code 集成，不修改模型权重，也不宣称通用自主进化。

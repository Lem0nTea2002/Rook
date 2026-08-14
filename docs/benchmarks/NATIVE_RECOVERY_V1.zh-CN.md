# Rook Native Task Set v1 与 Recovery Benchmark v1

## 目标

这组 benchmark 回答两个不同问题：

1. Rook 能否在未见的完整仓库历史 Issue 上独立完成长任务；
2. 用户确认的项目记忆能否在未见同类任务中减少重复错误。

二者共享固定仓库 commit、禁网 Linux 容器、隐藏验证、标准化轨迹和不可变报告，
但不共享被测工作区或 Session。

## 已实现边界

### Native

- 严格 30 任务目录与仓库、类别配额；
- 公开任务与私有 Validator 分离；
- Validator commitment 绑定目录、隐藏补丁和环境；
- Docker Shell 与 Validator 均固定镜像 digest、`network=none`、只读根文件系统、
  非 root、资源限制和进程树超时清理；
- API Key 只留在宿主 Provider，容器不继承宿主环境；
- smoke 取每仓库 1 个任务，pilot 取每仓库 3 个任务，formal 运行全部 30 个；
- Formal 只允许一次全局基础设施重试；
- 救援只能继续原失败工作区和 Session，最多两条、每条 300 字；
- 原始响应、补丁和 Validator 输出先执行秘密检测和脱敏，再保存制品。

### Recovery

- 严格 20/20/10/10 的 60 条轨迹配额；
- Gold 必须来自独立人工标签与依据引用，不能由 Detector 自标；
- 评分只允许一次，并输出 TP、FP、TN、FN、Precision、Recall、FPR；
- Detector 评分阶段 Provider 调用增量必须为 0；
- 普通成功误提示、基础设施误学习和重复机会都必须为 0。

### Memory A/B

- 真实项目记忆的 `rule + triggers` 与 content hash 使用同一规范化算法；
- 恰好 10 条 active、non-stale、用户确认记忆；
- 20 个任务，每条记忆对应两个未见任务；
- 两臂独立物化，稳定交替顺序，并校验初始工作区 SHA-256；
- Baseline 不加载记忆；Memory 只加载对应 active 记忆；
- stale、revoked、unconfirmed 作为负控制；
- 整对基础设施失败最多从干净工作区重试一次；
- 只有 20 个完整配对、负控制加载为 0、秘密泄漏为 0、初始哈希一致时，
  才能形成有效证据。

## 当前 readiness

截至 2026-08-01：

- Docker Server：29.0.1，Linux amd64；
- Native 严格冻结器、容器运行时、ScoreCard 和 CLI 已实现；
- 最终 3-task Native live smoke 已完整结束：3/3 干净终止、3/3 非空 Patch、权限中断 0、
  基础设施排除 0，但密封验证通过 0/3；
- 该有效 smoke 使用 34 次 Provider 请求，记录 377,029 input Token 和 30,737 output
  Token；Provider 未提供美元费用；
- Recovery 只读盘点覆盖 24 条日常轨迹和 35 条 Native 轨迹，其中 Detector 恢复机会 0，
  未达到 60 条冻结配额；
- Project Memory 已确认 10/10，A/B 任务已冻结 20/20，私有 Validator 离线验证通过；
- Native 30-task 诊断轮为 1/30，已明确排除在正式效果结论之外；
- 未执行 Memory pilot 或 Memory Formal；
- 未创建 GitHub Issue、PR 或其他写操作。

因此运行时小 Gate 已通过，Native 能力小 Gate 仍未通过；Memory v1 输入已经冻结，尚无
真实 A/B 效果数据。不能报告 Native 正式成功率或 Memory 改善比例。完整诊断见
`docs/benchmarks/NATIVE_DIAGNOSTIC_FAILURE_ANALYSIS_2026-07-31.zh-CN.md`。

## 命令

```text
rook benchmark native verify
rook benchmark native smoke
rook benchmark native run --phase pilot|formal
rook benchmark native rescue <experiment-id>
rook benchmark native report <experiment-id>
rook benchmark native reveal <experiment-id>

rook benchmark recovery verify
rook benchmark recovery score

rook benchmark memory verify
rook benchmark memory run --phase pilot|formal
rook benchmark memory report <experiment-id>
```

所有 live 命令都必须同时传入 `--allow-external` 和 `--allow-costs`，且固定使用
`deepseek/deepseek-v4-flash`。本地测试与 CI 只使用 Fake Provider。

# Memory Seed v1 审阅单

## 当前结论

10 个隔离开发 Seed 均已使用 `deepseek/deepseek-v4-flash` 真实执行，全部形成
`失败 → 修正动作 → 验证通过` 的唯一 RecoveryOpportunity。它们证明恢复检测、证据回指和
项目记忆存储链路可以工作；Seed 属于开发集，不能作为 Memory A/B 效果指标或未见 Holdout。

Seed 01–10 的规则已经用户逐条确认，并写入 10 条不可变 active 项目记忆。20 个未见 A/B
任务和 stale、revoked、unconfirmed 三类负控制已经冻结并通过离线验证。Memory Pilot 和
Formal 均未启动。

权威审阅数据位于 `benchmark/memory/v1/seed-review.json`：

- `evidence_state=ten_development_seeds_executed`；
- `activation_allowed=true`；
- active memory 为 10/10；
- awaiting review 为 0/10；
- Tool Schema fingerprint 为 `1764bd818dab06a14336505173a12050`。
- 10 条记录共包含 52 个 EvidenceRef，全部能解析到已接受 Seed 转录中的原始 event/part；
- 10 个 content hash 已根据规则正文和触发条件独立重算，结果全部与冻结 Catalog 一致。

## 审阅状态

| Seed | 建议主题 | 状态 |
|---|---|---|
| 01 | 先读相邻测试，再做目标验证与相关回归 | active |
| 02 | 路径失败后以目录/glob 证据纠正 | active |
| 03 | 通用命令失败后改用项目声明入口 | active |
| 04 | 任意编号或顺序改为语义不变量 | active |
| 05 | 显式随机源与多种子稳定性 | active |
| 06 | 最低支持版本兼容性 | active |
| 07 | 配置启用、禁用和默认路径 | active |
| 08 | 全局状态清理与组合运行 | active |
| 09 | 文档构建与 doctest 双验证 | active |
| 10 | 主要与替代输出后端验证 | active |

用户确认后，Seed 03–09 已使用各自 RecoveryOpportunity 的原始 EvidenceRef 生成不可变
记录。全部 10 条记录的 content hash 与 Tool Schema fingerprint 已写入冻结 Catalog。

## 有效执行结果

固定 Provider 为 `deepseek/deepseek-v4-flash`，未发生模型或 Provider 回退。

| Seed | 修改范围 | RecoveryOpportunity | 结果 |
|---|---|---|---|
| 01 邻近测试 | `src/rook_seed/slugify.py` | `verification_recovery` | passed |
| 02 路径恢复 | `src/rook_seed/config/settings_impl.py` | `alternative_solution` | passed |
| 03 项目入口 | `src/rook_seed/normalizer.py` | `verification_recovery` | passed |
| 04 语义不变量 | `tests/test_clusters.py` | `verification_recovery` | passed |
| 05 随机确定性 | `src/rook_seed/sampling.py` | `verification_recovery` | passed |
| 06 版本兼容 | `src/rook_seed/prefix.py` | `verification_recovery` | passed |
| 07 配置负路径 | `src/rook_seed/todos.py` | `verification_recovery` | passed |
| 08 状态清理 | `src/rook_seed/registry.py` | `verification_recovery` | passed |
| 09 Doctest | `src/rook_seed/formatting.py` | `verification_recovery` | passed |
| 10 多输出后端 | `src/rook_seed/renderers.py` | `verification_recovery` | passed |

10 条有效记录合计 78 次逻辑 Provider 请求、79 次实际请求、488,529 input tokens、
14,101 output tokens 和 247.108 秒 Agent 时延。Seed 10 包含 1 次同端点瞬态重试；其余
有效记录均为 0 次重试。美元费用未观测，不做估算。

本轮 Seed 04–09 的独立授权上限为 60 次逻辑请求和 72 次实际请求，实际使用 47/47，
6/6 passed。Seed 03 使用此前有效记录，7 个待审 Seed 的合计有效结果为 7/7 passed。

Seed 04 的一次初始运行在模型任务和测试通过后，因 Windows 默认 GBK 解码 UTF-8 Git Patch
而被标记为基础设施排除。修复后从干净夹具重跑通过；排除运行保持原始 `stopped` Manifest，
没有并入上述有效统计。

## 后续 Gate

1. 已完成 10 条规则确认和不可变记录生成。
2. 已冻结 20 个未见任务与三类负控制。
3. 已完成 `rook benchmark memory verify`，未调用模型。
4. 下一步单独授权 4-pair Memory Pilot。

任何未确认、stale 或 revoked 记忆被加载，都会使后续 A/B 证据无效。

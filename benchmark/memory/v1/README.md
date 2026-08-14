# Project Memory A/B v1

该目录保存 10 条用户确认的冻结记忆、20 个未见任务的公开配对目录和 3 条负控制。
Seed task 不进入 A/B 指标；每条记忆对应两个未见任务。

## 当前状态

- 10 条记忆均为 `active`、`non-stale`，并绑定内容哈希和 Tool Schema 指纹；
- 20 个任务覆盖 6 个仓库，公开目录不包含 gold patch、隐藏测试、Validator 命令或期望输出；
- 最新 v8 Pylint/Xarray 定向 2-pair：Baseline 0%、Memory 0%，Validator 成功臂为 0；
- M1、M2、M3 依次需要独立的真实模型与费用授权；20-pair Formal 保持暂停。

## 冻结指纹

- Catalog：`33a5692e5a50b900eeb4de5f2856b52ac628d7f82bfa013618f1a2d099a2e225`；
- Validator commitment：`3382235b9db99bd21abbb96e4f55a81c2719467488422d5b836933180634cafc`；
- 数据集快照：`311c90a7a28038402ff17fcfc09d7b866d5c60cfa0909d70163fb0f4b40fb299`。

零模型调用验证公开目录：

```powershell
rook benchmark memory verify --catalog benchmark/memory/v1/catalog.json
```

私有 Validator、测试补丁和镜像命令保存在仓库外。带 `--validators` 的严格验证和
`memory run` 只能在单独授权的评测阶段执行。Baseline 臂关闭记忆加载；Memory 臂只加载
对应的 active、non-stale 记忆。任何负控制加载、初始工作区哈希不一致或秘密泄漏都会使
证据失效。

完整证据边界见 `docs/benchmarks/MEMORY_AB_FREEZE_V1.zh-CN.md`，失败演进见
`docs/benchmarks/MEMORY_PILOT_V1_2026-08-01.zh-CN.md`。

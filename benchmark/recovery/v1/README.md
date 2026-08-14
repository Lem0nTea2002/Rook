# Recovery Benchmark v1

该目录用于保存一次性冻结的 60 条真实、脱敏 Rook 轨迹：

- 20 条失败后恢复并通过验证；
- 20 条失败但未恢复；
- 10 条 Provider、网络、沙箱或主机基础设施故障；
- 10 条无失败的普通成功。

当前状态：尚未冻结。

2026-07-31 对当前 `.rook/sessions` 的只读盘点结果为 11 条轨迹：

- `state_verified_success`：1；
- `completed_without_verifier`：2；
- `unknown`：8；
- RecoveryDetector 识别到的恢复机会：0。

因此现有证据不足以生成 Recovery v1。必须继续从真实 Native 或日常 Rook
运行中收集轨迹，并由人工独立标注；不得用 Detector 输出反向生成 Gold。

准备命令：

```powershell
python scripts/prepare_recovery_benchmark.py inventory `
  --session-root .rook `
  --output .rook/benchmarks/recovery-v1/inventory.json

python scripts/prepare_recovery_benchmark.py freeze `
  --session-root .rook `
  --labels <人工标注文件> `
  --output benchmark/recovery/v1/traces.jsonl
```

配额不足、重复轨迹、未知字段或重复冻结都会 fail-closed。

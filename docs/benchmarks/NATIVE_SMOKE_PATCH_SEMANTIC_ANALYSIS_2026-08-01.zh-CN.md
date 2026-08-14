# Native Smoke 三份非空 Patch 语义分析

## 证据边界

本文分析 `native-smoke-20260731T162350416405Z-227ddc06` 的三份非空 Patch。只使用
公开 Issue 文本、公开仓库源码与测试、模型回复、模型 Patch 和公开终态 reason code；不读取
密封 Validator 命令、输出或 gold patch。

## 结论

| 任务 | Patch 实际内容 | 公开问题语义是否闭环 | 主要缺口 |
|---|---|---|---|
| pytest #9877 | 新增一个失败复现测试 | 否 | 已定位根因，但没有修改 `LogCaptureHandler.reset()` |
| scikit-learn #10307 | 新增根目录复现脚本 | 否 | 已定位分支条件，但没有修改指标实现或正式测试 |
| Sphinx #10048 | 修改英语生成路径、POT 和一个 HTML 测试 | 部分 | 英语主路径成立，翻译和替代输出验证未闭环；另有测试补丁应用冲突 |

### pytest #9877

公开问题要求 `caplog.clear()` 后 `caplog.get_records("call")` 与当前记录保持一致。模型准确
指出 stash 保存旧列表引用，而 `LogCaptureHandler.reset()` 用 `self.records = []` 替换列表。
但 Patch 只有 `testing/logging/test_repro_9877.py`，没有修改 `src/_pytest/logging.py`；新增测试
仍会失败。因此这是“诊断正确、实现未完成”，不是错误修复。

模型在最终回复中明确说明 Provider 请求预算已用尽。对应改进不是扩大答案篇幅，而是要求
Agent 在预算内优先完成 `复现 → 最小生产修改 → 目标验证`，并把临时复现并入相邻正式测试。

### scikit-learn #10307

公开问题要求 `labels` 包含 0 时仍只对请求列做 macro 平均。模型正确定位到
`precision_recall_fscore_support` 对扩展后 labels 的比较会跳过列裁剪，但 Patch 只有
`repro_10307.py`，生产实现和正式测试均未改变。该文件还位于仓库根目录，不在任务声明的
`sklearn/`、`doc/`、`examples/` 允许范围内。

因此这同样是“复现成功、修复未落盘”。仅凭最终回复中的拟议代码不能计为 Patch，也不能
通过公开 API 行为验证。

### Sphinx #10048

Patch 将 HTML、HTML5、浏览器端 doctools 和 POT 的英语 key 从 `headline` 改为 `heading`，
并更新现有 HTML 断言；模型报告的公开目标测试通过。因此英语主路径比前两项完整。

但公开 Issue 明确提醒翻译字符串也受影响。Patch 保留所有语言目录中的旧 msgid 和生成
JavaScript，且只运行一个默认英语 HTML 测试；这意味着本地化页面可能回退到英语，新旧翻译
制品也没有经过代表性验证。它应被记为“部分语义闭环”，而不是简单等同于全错。

终态 `hidden_patch_conflict` 发生在 Rook 将密封测试补丁应用到模型 Patch 之后、执行密封测试
之前。模型已经修改公开测试中的同一断言，因此该 reason code 只证明测试补丁无法干净应用，
不能单独证明生产修改错误。这是评测器需要隔离“模型改测试”和“测试补丁物化冲突”的证据。

## Seed 选择

本轮选择以下三个已审阅 Seed 做真实恢复验证：

- `seed-01-neighbor-tests`：约束 Agent 不停在复现文件，必须修改生产实现并完成目标与回归验证；
- `seed-02-resolve-path`：验证错误路径后是否使用目录或 glob 证据恢复，不重复猜测；
- `seed-10-output-backends`：验证主后端通过之外，是否覆盖替代输出后端。

没有选择 `seed-09-doctest-source`：三份 Patch 都不涉及 doctest，执行它不能解释本次失败。
Seed 运行只用于生成真实 RecoveryOpportunity，不是 Memory A/B，也不产生 Formal 指标。

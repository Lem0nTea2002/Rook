# Native 30-task 诊断轮失败分析

## 证据边界

分析来源为本地诊断轮
`native-formal-20260731T092400919129Z-96167c89`。该轮不能作为 Formal：救援缺陷使
3 个 unassisted Session 出现额外用户消息，原始证据不再满足不可变要求。本文只读取
公开 Session 事件、Patch 统计和终态 Manifest，不读取隐藏测试命令、输出或 gold patch。

## 结果

30 个任务中 1 个通过、29 个失败。29 个失败的主归因互斥统计如下：

| 主归因 | 数量 | 含义 |
|---|---:|---|
| `noninteractive_permission_pause` | 19 | AUTO 评测在 Shell 权限处等待人工输入，任务提前结束 |
| `budget_exhausted_without_patch` | 8 | 达到 Provider 请求上限时仍没有 Patch |
| `patch_validation_miss` | 1 | 形成 Patch，但未通过密封验证 |
| `regression_introduced` | 1 | 形成 Patch，但新增公开回归 |

另外有 3 个任务带 `evidence_contaminated` 标记；该标记与主归因重叠，不重复计入
29 个任务。失败任务中只有 10/29 干净终止、3/29 留下非空 Patch、19/29 发生权限中断。

按仓库拆分：

- pytest：5 个权限暂停、3 个无 Patch 耗尽预算、1 个验证未通过、1 个新增回归；
- scikit-learn：10 个全部在权限等待处提前结束；
- Sphinx：4 个权限暂停、5 个无 Patch 耗尽预算；另有 1 个任务通过。

因此本轮的首要问题是执行协议和 Windows 工作区路径，不应把 29 个失败全部解释为
模型能力不足。

## 根因与修复

### 1. 非交互 AUTO 与权限等待冲突

Native Shell 已运行在固定 digest、禁网、非 root、一次性工作区容器中，但仍沿用交互式
AUTO Shell 白名单。模型生成带组合操作符或诊断命令时，19 个任务进入等待用户确认，
Formal 没有用户可回答，最终留下不完整工具序列。

修复后：

- Native 专用 AUTO 允许容器工作区内 Shell；
- 其他需要人工确认的动作在非交互评测中明确拒绝并返回 Tool Result，不进入等待状态；
- 本地 TUI、飞书、微信和一般 AUTO 权限策略保持不变。

### 2. Windows 深层源码超过 MAX_PATH

运行目录已缩短 run id，但内部仍拼接完整 task id。诊断样本中的深层 scikit-learn 文件
路径达到 265 字符，表现为 `ls/glob` 能列出、`view/grep` 却报告路径不存在。内部工作区
组件改为运行私有的固定 `w` 后，同一路径降至 234 字符；task id 仍保留在 Manifest。

### 3. 空 Patch 被混入普通验证失败

旧流程会对空 Patch 继续物化验证工作区并运行密封验证，最终只得到笼统的
`execution_nonzero_exit`。新流程在 Validator 前直接返回 `agent_patch_empty`，避免浪费
验证时间，也让 ScoreCard 能区分“没有形成修改”和“补丁语义错误”。

### 4. Agent 缺少显式完成契约

公开 Prompt 现在要求：根据公开 Issue 定位和复现、形成最小修改、运行针对性验证、用
`git diff` 确认非空补丁。Prompt 不包含隐藏命令、隐藏测试名或预期输出。

### 5. 权限拒绝分支重复消费同一 Tool Call

第一次修复后的 live 诊断暴露出两个运行时缺陷：容器路径 `/workspace` 在 Windows 宿主
被错误解释为 `D:\workspace`，以及同步/异步工具循环在直接拒绝后没有推进调用索引。
结果是同一个 Shell 调用被重复写入约 8,600 次，外层进程也不能自行结束。

修复后：

- Native Shell 将 `/workspace` 和其子路径映射到容器内相对工作目录；
- 权限预检只判断宿主工作区边界，不再把容器 cwd 当作宿主绝对路径；
- 同步和异步拒绝分支都只消费一次 Tool Call；
- 回归测试确认每个 call id 最多产生一个 Tool Result。

### 6. Provider 上限不是干净终止

第二轮诊断证明模型可以形成 Patch，但会在第 12 次 Provider 请求后由硬上限截断。旧指标
曾把这种 `provider_call_limit` 误记为干净终止。现在只有 `finish_reason` 为 `stop` 或空值
才记为干净终止。

Native 运行额外保留最后一次 Provider 请求作为禁用工具的收尾请求；若此前 Todo 尚未完成，
收尾后不再发起第 13 次 Todo 自检。该策略不修改本地 TUI 默认预算。OpenAI-compatible
响应若声明 `tool_calls` 却因非法 JSON 导致整组调用被丢弃，只允许一次受控重试，第二次
仍失败则以错误终态结束。

## 下一道 Gate

只运行 3 个针对性任务：分别覆盖旧路径超限、旧权限暂停和已形成 Patch 但验证失败的
类型。进入下一次 30-task Formal 前必须同时满足：

- 3/3 无权限等待；
- 3/3 干净终止；
- 深层文件读取错误为 0；
- 至少 2/3 形成非空 Patch；
- 不出现基础设施排除或证据污染。

成功率不是本次小规模复测的唯一 Gate；即使能力任务仍失败，只要执行链指标达到上述
要求，也能证明运行时修复有效。后续 Formal 必须从干净工作区和全新 Session 从零开始。

## 小规模复测结果

最终有效 smoke 为
`native-smoke-20260731T162350416405Z-227ddc06`。此前几轮只用于定位权限循环、路径映射、
非法 Tool Call 和预算收尾缺陷；被人工停止或使用旧终止口径的轮次均不作为 Gate 证据。

| 任务 | 能力终态 | 干净终止 | 权限中断 | Provider 请求 | Tool Call | Patch |
|---|---|---:|---:|---:|---:|---:|
| pytest #10051 | `validation_failed` | 是 | 0 | 12 | 19 | 505 bytes |
| scikit-learn #10377 | `validation_failed` | 是 | 0 | 12 | 18 | 874 bytes |
| Sphinx #10048 | `validation_failed` | 是 | 0 | 10 | 20 | 3,930 bytes |

运行时 Gate 通过：

- 3/3 无权限等待，3/3 干净终止；
- 3/3 轨迹、终态 Manifest 和容器清理完整；
- 3/3 形成非空 Patch；
- 每个 Tool Call 恰好对应一个 Tool Result，重复失败再次尝试为 0；
- 旧 Windows 深层文件读取错误为 0；scikit-learn 中一次 `/root` 请求被正确作为越界路径
  拒绝，不属于 MAX_PATH 故障；
- 基础设施排除 0，证据污染 0，实验终态为 `completed`。

能力 Gate 没有通过：3 个 Patch 均未通过密封验证，能力成功为 0/3。因此该 smoke 只证明
执行协议、补丁留存和干净终止得到改善，不能证明 Rook 已能解决这些真实 Issue，也不能
作为简历成功率。30-task Formal 和 Memory A/B 均未重新运行。

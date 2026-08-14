# Rook 三分钟面试演示

这套演示把 Rook 的三个核心价值串成一条可讲清楚的路径：

`Coding Task → Tool Call → 权限确认 → 恢复检测 → 用户审阅 → 项目记忆 → 后续复用 → Skill 考试 → Gate → 人工审批 → 部署 → drift → rollback`

前半段使用当前配置的真实 Provider 完成一个小型 Coding Task，可能产生少量模型费用；后半段使用确定性 Fake Agent 验证 Rook Forge 控制面，不访问网络、不调用模型，也不冒充真实模型效果。

## 一条命令准备

在 Rook 仓库根目录运行：

```powershell
pwsh -File scripts/run_three_minute_demo.ps1
```

脚本会创建一个独立 Git 工作区，展示需要粘贴给 Rook 的 Prompt，然后启动 TUI。退出 TUI 后，脚本自动执行 Forge 生命周期并打印最终证据摘要。所有文件都保存在 `.rook/interview-demo/run-<id>`，不会修改当前源码工作树。

只准备演示仓库、不启动模型：

```powershell
pwsh -File scripts/run_three_minute_demo.ps1 -PrepareOnly
```

## 三分钟讲解节奏

### 0:00–0:20：问题与定位

> Rook 是本地 Coding Agent；Rook Forge 把 Skill 当作需要考试、审批、部署和回滚的软件版本。重点不是“模型能调用工具”，而是每次能力变化都有可验证证据和发布边界。

### 0:20–1:10：Coding Task 与 Tool Call

把脚本输出的 Prompt 粘贴到 TUI：

```text
读取 README.md，修复折扣计算，只修改 src/pricing.py，并运行 python -m pytest -q 验证。
```

演示时指出：

1. Rook 读取任务和源码；
2. Tool Call 与结果持续显示；
3. 写文件或执行受控命令触发权限 Picker；
4. `Allow once` 默认高亮，但仍需 Enter 明确确认；
5. 测试通过后用 `/diff` 查看精确修改。

### 1:10–1:35：确认式学习

任务中出现“失败后用修正方案验证通过”时，指出 `LEARN · RECOVERED FAILURE` 卡片，
并依次展示：

```text
/learn
/learn last
/learn save
/memory
```

解释边界：恢复检测由标准化轨迹确定性完成，检测阶段模型调用为 0；`/learn last` 只有在
用户明确要求审阅时才调用 Provider；`/learn save` 产生用户确认的不可变项目记忆。
跨项目步骤化经验使用 `/learn forge` 进入 `quarantined` Candidate，仍需后续考试与审批。

现场任务没有自然产生恢复机会时，直接打开
`docs/benchmarks/MEMORY_M0_READINESS_2026-08-03.zh-CN.md` 与
`docs/benchmarks/MEMORY_PILOT_V1_2026-08-01.zh-CN.md`：前者证明离线控制链，后者保留
Baseline 0% / Memory 0% 的最新定向失败。Memory Formal 尚未执行，因此本段只演示产品
机制和证据边界，不宣称记忆已经改善真实任务。

### 1:35–2:35：Rook Forge 发布链路

退出 TUI 后，脚本执行：

```powershell
rook eval demo
```

按输出解释：

1. Candidate v1/v2 先进入隔离存储；
2. Direct、Transfer、Regression、Adversarial 四类考试生成 ScoreCard；
3. Gate 只授予上线资格，不能自动激活 Skill；
4. 人工审批后分别部署到 Rook/Codex；
5. 修改受管 `SKILL.md` 后状态变为 `drifted`；
6. 事务回滚把两个目标恢复到已批准的 v1。

### 2:35–3:00：结果与证据边界

> Skill Formal 在冻结套件上完成 72 次真实 `gpt-5.4-mini` 调用和 36 组配对，成功率从 25.0% 提升到 94.4%，中位时延下降 5.8%，Token 下降 15.2%，新增回归 0。Memory Formal 尚未执行，当前 Pilot 数据只用于诊断，简历暂不填写项目记忆的 X 指标。刚才的离线 Demo 证明治理链路正确。

最后打开：

```text
.rook/interview-demo/run-<id>/forge/run-<id>/demo-summary.md
```

其中包含 Gate、审批、双目标部署、drift 检测、rollback 和内容哈希的一致性证据。

## 演示失败时如何处理

- Provider 不可用：使用 `-PrepareOnly`，只讲已经提交的 Formal 证据和离线 Forge Demo。
- TUI 未触发权限：在 ASK 模式下让任务写文件或运行 Shell，不要切换 FULL。
- 时间不足：跳过真实 Coding Task，直接运行 `rook eval demo`，但要明确这是控制面演示。
- 不要在演示中展示 API Key、系统凭据、微信/飞书 Secret 或原始模型会话文件。

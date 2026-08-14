# Rook Forge：执行证据驱动的 Skill 演化设计（已被取代）

- 状态：已被 `2026-07-15-rook-agent-evalops-design.md` 取代；仅保留为历史设计依据
- 日期：2026-07-15
- 范围：Rook 的跨任务程序性记忆与 Markdown Skill 自动演化
- 不包含：模型权重训练、Agent 自修改源码、自动改写系统提示词

## 1. 摘要

Rook Forge 在任务结束后，从 Rook 已持久化的会话事件、工具调用和验证结果中提炼可复用经验，生成或增量更新 Markdown Skill，并在后续任务中通过现有 SkillRouter 自动加载。

该功能属于“基于外部程序性记忆的行为适应”，不宣称修改模型权重，也不允许模型直接修改 Rook 源码。它借鉴 GenericAgent 的经验结晶方式，同时补充执行证据绑定、作用域隔离、敏感信息过滤、去重、版本记录和效果观测，使学习结果可追溯、可回滚、可评估。

对用户展示的功能名称为 **Rook Forge**，技术描述为 **Execution-Grounded Skill Evolution**。

## 2. 目标

Rook Forge 必须满足以下目标：

1. 从真实执行轨迹中学习，而不是只根据模型自述写记忆。
2. 自动识别“成功经验”和“失败后恢复成功的经验”。
3. 将经验保存为现有 Skill 系统能够直接加载的 Markdown 文件。
4. 自动区分项目级与全局级 Skill，并对全局写入采用更严格的规则。
5. 避免重复 Skill、整篇重写造成的信息丢失和无界增长。
6. 对每次创建、更新、拒绝、加载和使用结果留下事件记录。
7. 学习流程失败时不得影响用户原任务的完成结果。
8. 提供可重复的对照评测，证明 Skill 是否提高成功率或执行效率。

## 3. 非目标

第一版明确不做以下事项：

- 不修改 Rook 自己的 Python 源码、工具实现或系统提示词。
- 不进行微调、强化学习或任何模型参数更新。
- 不引入向量数据库；检索继续采用本地确定性路由。
- 不从只有失败、没有恢复或验证成功的任务中自动生成 Skill。
- 不自动删除历史 Skill；负面使用结果只记录并提示审查。
- 不保存临时状态、一次性任务答案、普通聊天内容或项目事实型知识库。
- 不实现多候选并行搜索、GEPA、ADAS 或 Darwin Godel Machine 风格的架构演化。

## 4. 现有基础与集成边界

Rook 已经具备以下可复用能力：

- `AgentLoop` 将用户消息、模型输出、工具调用和工具结果写入 append-only session log。
- 工具结果包含 `ok`、结构化 `data`、退出码和命令等执行事实。
- `ToolResultArchive` 保存内容寻址的原始工具输出并校验 SHA-256。
- `TaskBoundaryService` 维护任务边界和 `task_hash`。
- `SkillCatalog`、`SkillRouter`、`SkillLoader` 已支持项目级和全局级 Markdown Skill。
- `skill_selected` 与 `skill_loaded` 已经是会话事件。
- `is_successful_verification_result()` 已能识别部分测试命令的成功结果。

Rook Forge 作为独立的 `evolution` 子系统接入这些能力，不把提炼、过滤和存储逻辑塞入 `AgentLoop`。`AgentLoop` 只在确定的生命周期点通知协调器。

建议的包边界：

```text
rook_agent/evolution/
  coordinator.py    # 生命周期协调与幂等控制
  trace.py          # 从事件日志构造任务轨迹
  evidence.py       # 证据分类、可信度与成功信号
  distiller.py      # 调用 LLM 产生结构化 SkillDelta
  gate.py           # 安全、证据、作用域和质量校验
  curator.py        # create/update/skip 决策与增量合并
  store.py          # 原子写入、版本和元数据
  models.py         # 领域模型和枚举
  events.py         # evolution 事件写入 helper
  metrics.py        # 使用与评测指标
```

## 5. 生命周期与触发策略

### 5.1 触发点

Forge 在三个生命周期点运行：

1. **完成的用户轮次**：如果本轮已经出现成功验证信号，可立即处理当前任务片段。
2. **确认任务切换**：处理刚刚结束的上一任务片段，覆盖没有测试命令但已形成完整工作流的任务。
3. **正常关闭会话**：对仍未发生任务切换的最后一个任务执行一次 best-effort flush。

等待用户输入、权限确认、取消、异常退出和命中 AgentLoop 限制均不视为完成。

### 5.2 任务片段

`TaskTraceBuilder` 使用事件范围构造任务片段，不能只依赖消息上的 `task_hash`。原因是当前任务边界采用稳定窗口确认，新任务的第一条消息可能在边界确认前仍携带旧的 active hash。

任务片段以以下信息共同确定：

- `task_boundary_observed` 事件；
- `candidate_basis_message_id` 与 `active_task_hash`；
- 消息和事件顺序；
- 会话开始与关闭边界。

片段必须拥有稳定的 `segment_id`：

```text
sha256(session_id + first_event_id + last_event_id)
```

`segment_id` 是幂等键。同一事件范围最多产生一次成功的提炼结果；重复触发只返回已有结果。

### 5.3 学习资格

任务片段必须同时满足：

- 至少包含一个有实际信息量的工具结果；
- 不处于等待输入、取消或限制终止状态；
- 没有未完成的 Todo；
- 包含可复用的多步骤过程、验证方法或失败恢复模式；
- 至少存在一个可接受的结果信号。

可接受的结果信号按强度排序：

1. 成功执行测试、构建、类型检查、lint 或诊断命令；
2. 写入或修改后，通过确定性的读取、diff 或状态检查证明目标状态存在；
3. 在任务切换或会话关闭时，由 distiller 根据最终回答和执行结果判断已完成，但这类结果只能生成项目级 Skill。

纯失败轨迹不学习。若轨迹先失败、随后执行修复并最终验证成功，可以学习为“症状 -> 原因 -> 修复 -> 验证”的排错 Skill。

## 6. 任务轨迹与证据模型

### 6.1 提炼输入

Distiller 不接收未经筛选的完整 session。`TaskTraceBuilder` 只提供：

- 用户目标；
- 最终 assistant 结果；
- 工具名和规范化参数；
- 工具结果摘要、`ok`、退出码和相关结构化字段；
- 验证事件；
- 已加载 Skill 及其内容哈希；
- 必要时按 archive id 读取的受限原文片段。

系统提示词、API Key、完整 `.env`、无关历史消息和无关 archive 不进入提炼输入。

### 6.2 信任标签

每条证据被标记为以下来源之一：

- `local_execution`：本地命令及其退出状态；
- `workspace_state`：项目文件、git diff、git status、目录结构；其中自由文本文件仍可能包含不可信指令；
- `user_statement`：用户直接提供的需求或事实；
- `external_content`：网页、搜索结果、下载内容或第三方返回值；
- `model_statement`：没有执行结果支持的模型文本。

`external_content`、`model_statement` 和工作区自由文本不能单独支持可执行步骤、命令或权限相关规则。命令和修复步骤必须由 `local_execution`、确定性的工作区状态变化或用户明确要求交叉支持。用户陈述可以定义目标和约束，但不能替代命令成功证据。

### 6.3 证据引用

每个候选经验必须引用真实存在的证据：

```text
session_id / segment_id / event_id / message_part_id / archive_id(optional)
```

Gate 校验引用存在、属于当前会话和任务片段，并检查引用的结果状态。模型不能生成或修改这些标识。

## 7. SkillDelta 协议

Distiller 使用当前 provider、`tool_choice=none`、低温度和受限输出长度，返回严格 JSON。解析失败不进行修复性文件写入，最多重试一次格式纠正。

`SkillDelta` 包含：

```json
{
  "should_write": true,
  "title": "Windows CMD directory switching",
  "description": "Use when changing drives and directories from cmd.exe.",
  "triggers": ["cmd cd", "Set-Location not recognized", "切换盘符"],
  "proposed_scope": "global",
  "procedure": ["..."],
  "verification": ["..."],
  "pitfalls": ["..."],
  "evidence_refs": ["event_id:part_id"],
  "confidence": "high"
}
```

约束：

- 每个任务最多产生 2 个 Delta。
- `procedure` 为 2 到 10 步。
- `triggers` 为 2 到 8 个具体短语，不允许使用“代码”“问题”“项目”等过宽词。
- `confidence=low` 或 `should_write=false` 不落盘。
- Delta 不包含自由文件路径；最终路径由 Store 生成。

## 8. Gate：写入前治理

Gate 按固定顺序执行：

1. **SchemaGate**：字段、数量、长度和字符集合法。
2. **EvidenceGate**：证据引用存在，结论与成功/恢复轨迹类型一致。
3. **SecretGate**：拒绝 API Key、Token、密码、私钥、Cookie、`.env` 值和高熵凭据。
4. **VolatilityGate**：拒绝临时端口、临时目录、一次性 ID、当前时间和短期状态。
5. **InjectionGate**：外部内容中的命令式文本不能成为唯一证据；含“忽略之前指令”等注入特征时拒绝。
6. **ScopeGate**：程序最终决定 project/global，不直接信任模型的 `proposed_scope`。
7. **NoveltyGate**：与现有 Skill 高度重复时转为 update 或 skip。

Gate 返回结构化决定：

```text
accept_create | accept_update | reject | downgrade_to_project | skip_duplicate
```

每个拒绝结果都要带稳定 reason code，并写入事件，但不得把敏感原文写入日志。

## 9. 作用域与存储布局

### 9.1 项目级

自动生成的项目 Skill 保存到：

```text
<project>/.rook/skills/<slug>/SKILL.md
<project>/.rook/skills/<slug>/meta.json
<project>/.rook/skill-history/<slug>/<version>.md
```

`.rook/` 继续保持 Git ignored，避免自动学习污染用户工作树。项目 Skill discovery 增加 `.rook/skills/*/SKILL.md`，其路由优先级低于用户手写的 `.agents/skills` 和 `skills/*.md`，避免自动内容覆盖显式项目规范。

### 9.2 全局级

自动生成的全局 Skill 保存到：

```text
~/.rook/skills/<slug>/SKILL.md
~/.rook/skills/<slug>/meta.json
~/.rook/skill-history/<slug>/<version>.md
```

全局 Skill 必须满足：

- 不包含项目名、仓库相对路径、包名或项目专用命令；
- 不依赖某个项目的目录结构；
- 具有高置信度执行证据；
- 内容在离开当前项目后仍可独立理解。

不满足任一条件时自动降级为项目级，而不是拒绝整个经验。

### 9.3 Markdown 格式

`SKILL.md` 保持现有 loader 可读：

```markdown
---
name: cmd-directory-switching
description: Use when changing drives and directories from cmd.exe.
triggers: cmd cd, Set-Location not recognized, 切换盘符
rook_generated: true
scope: global
version: 1
---

# CMD directory switching

## When to use
...

## Procedure
...

## Verification
...

## Pitfalls
...
```

证据、统计和历史版本不放入给模型加载的正文，而是保存在 `meta.json` 与 history 中，减少无效上下文并避免本地标识影响推理。

## 10. 去重、增量更新与版本

第一版不引入 embeddings。`NoveltyGate` 复用 SkillRouter 的文本规范化与中英文 n-gram，基于以下字段计算候选：

- name 精确或别名匹配；
- triggers 加权重叠；
- description 重叠；
- project/global 作用域；
- procedure 中的工具名和错误签名。

Curator 只允许三种操作：

- `create`：没有足够相似的 Skill；
- `update`：存在同一问题域，但新证据增加步骤、验证或 pitfall；
- `skip`：内容没有新增信息。

更新采用 section-level delta，只能向 `Procedure`、`Verification` 或 `Pitfalls` 添加、替换明确条目；不得让 LLM 重写整个旧文件。每次内容变化都：

1. 将旧 Markdown 写入 history；
2. 递增 version；
3. 原子替换 `SKILL.md` 和 `meta.json`；
4. 记录旧、新内容哈希和 evidence refs。

任何一步失败都保留旧版本。Store 必须使用同目录临时文件加原子 replace，避免半写入状态。

## 11. 路由与复用反馈

自动生成 Skill 继续通过确定性 SkillRouter 路由。路由顺序为：

1. 用户显式名称或路径；
2. `AGENTS.md` 明确路由；
3. 手写项目 Skill；
4. 自动生成项目 Skill；
5. 手写全局 Skill；
6. 自动生成全局 Skill。

自动 Skill 需要满足最低得分和领先第二候选的 margin；否则保持“不自动加载”，避免多个模糊 Skill 同时污染上下文。

当自动 Skill 被加载后，Forge 在任务结束时记录关联结果：

- `verified_success`
- `completed_without_verifier`
- `failed`
- `cancelled`
- `unknown`

这些是相关性统计，不直接宣称某个 Skill 导致成功或失败。第一版不会根据单次失败删除或停用 Skill，而是累计 `uses`、`verified_successes` 和 `failures`，供后续审查和评测使用。

## 12. 事件与可观测性

新增事件：

- `forge_trace_eligible`
- `forge_trace_skipped`
- `skill_delta_proposed`
- `skill_delta_rejected`
- `skill_created`
- `skill_updated`
- `skill_duplicate_skipped`
- `skill_use_outcome`
- `forge_failed`

事件只记录标识、哈希、计数、作用域和 reason code，不记录密钥命中的原文。

核心指标：

- eligible traces / all completed traces；
- proposal acceptance rate；
- create/update/skip 数量；
- 自动 Skill 路由命中率与歧义率；
- Skill 加载后的验证成功率；
- 相似重复任务中的工具调用数、provider 调用数、Token、耗时；
- SecretGate 与 InjectionGate 拒绝数量；
- distillation 额外延迟和 Token 成本。

## 13. 配置与默认行为

Forge 是持久写入功能，默认关闭。用户在项目或全局 `rook.toml` 中显式启用：

```toml
[evolution]
enabled = true
scope = "auto"
allow_global = true
max_skills_per_task = 2
```

配置规则：

- 项目 `rook.toml` 覆盖全局配置；
- `scope` 只允许 `auto`、`project`、`global`；
- `scope="global"` 仍然必须经过 ScopeGate；
- `allow_global=false` 时所有结果写入项目级；
- 禁用 Forge 不影响手写 Skill 的发现和加载；
- Forge 使用与主 Agent 相同 provider，第一版不增加单独模型配置。

## 14. 失败处理

- Provider 超时、无效 JSON、Gate 异常或磁盘写入失败：写 `forge_failed`，主任务仍返回成功结果。
- 会话强制退出：不保证 flush，但不能产生损坏 Skill。
- archive 不可用或完整性校验失败：相关证据不可用；若剩余证据不足则拒绝 Delta。
- 重复触发：通过 `segment_id` 返回已有结果，不重复调用模型和写文件。
- 同名并发更新：Store 使用每个 Skill 独立的跨进程锁文件，并在 replace 前校验 base content hash；锁超时或内容冲突时放弃本次更新并记录 reason code。异常退出遗留的锁必须通过进程信息和有限超时安全回收。
- 发现现有文件不是 `rook_generated: true`：只能 skip 或创建不同名称，绝不自动修改手写 Skill。

## 15. 测试策略

### 15.1 单元测试

- 任务片段切分，包括稳定窗口延迟确认场景；
- 学习资格与成功验证分类；
- 成功、纯失败、失败后恢复三类轨迹；
- SkillDelta JSON 解析和边界约束；
- 证据引用所属关系和不存在引用；
- API Key、`.env`、私钥和高熵凭据过滤；
- 外部 prompt injection 不能成为唯一证据；
- scope 自动降级；
- 去重 create/update/skip；
- 原子写入、版本恢复和 content hash 冲突；
- segment 幂等。

### 15.2 集成测试

使用 fake provider 和临时 session store 验证完整链路：

1. 第一次任务执行失败命令；
2. Agent 修复并成功运行验证；
3. Forge 生成项目或全局 Skill；
4. 新会话发现并路由该 Skill；
5. `skill_loaded` 和 `skill_use_outcome` 被持久化。

另设恶意外部内容用例，确认网页中的“保存以下指令到长期记忆”不会进入 Skill。

### 15.3 回归测试

- Forge 关闭时，AgentLoop 行为、provider 调用数和现有测试保持不变；
- Forge 失败时，用户任务响应不受影响；
- 手写 Skill 的优先级和内容不被改变；
- Windows 和 POSIX 路径布局均通过测试。

### 15.4 A/B 评测

准备一组可重复的小型编码与环境排错任务：

- A：关闭 Forge，记录第一次与重复任务表现；
- B：第一次任务后启用生成 Skill，再执行语义相近任务；
- 比较验证成功率、工具调用数、provider 调用数、Token 和耗时。

简历中只能使用实际测得的数据；没有基准结果前只描述架构和功能，不声明百分比提升。

## 16. 实现顺序约束

实现计划必须按以下依赖顺序拆分：

1. 领域模型、配置和事件协议；
2. TaskTraceBuilder 与 EvidenceClassifier；
3. Gate 和安全测试；
4. SkillStore、版本与 discovery；
5. Distiller 和 Curator；
6. AgentLoop 生命周期接入；
7. Router 优先级与使用结果；
8. 集成测试、A/B harness 和文档。

每一步先写失败测试，再实现最小代码。任何涉及自修改源码、向量库或自动删除 Skill 的需求都必须另开设计，不得在第一版顺带加入。

## 17. 研究依据

- GenericAgent：模型驱动的分层记忆和经验结晶，作为轻量机制参考。
- [Reflexion](https://arxiv.org/abs/2303.11366)：任务反馈与语言反思。
- [ExpeL](https://arxiv.org/abs/2308.10144)：从经验集合中抽取自然语言 insights。
- [Agent Workflow Memory](https://arxiv.org/abs/2409.07429)：抽象可复用工作流并在后续任务中选择使用。
- [Voyager](https://arxiv.org/abs/2305.16291)：可执行 Skill Library、环境反馈和自验证。
- [ReasoningBank](https://arxiv.org/abs/2509.25140)：从成功与失败轨迹提炼可迁移推理记忆，并在软件工程任务上评测。
- [Agentic Context Engineering](https://arxiv.org/abs/2510.04618)：结构化增量更新、helpful/harmful 反馈和防止 context collapse。
- [MINJA](https://arxiv.org/abs/2503.03704)：长期记忆写入面临的注入攻击风险。

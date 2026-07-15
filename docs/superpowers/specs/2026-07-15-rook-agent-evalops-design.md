# Rook Agent EvalOps：Skill 纵向评测与演化准入设计

- 状态：已批准，待实现计划
- 日期：2026-07-15
- 产品定位：面向自适应 LLM Agent 的 Skill 纵向评测与演化准入框架
- 首批被测 Agent：Rook、Codex CLI、Claude Code CLI
- 接入方式：CLI 黑盒适配优先，SDK 接入后置

## 1. 摘要

Rook 不再以“另一个 Coding Agent”作为核心定位，而是作为 Agent EvalOps 框架，回答一个更具体的问题：自动生成或人工提供的 Skill，是否真的让工具型 Agent 变得更有效、更稳定且更安全。

Rook 以任务执行结果、工具调用轨迹和外部验证器为主要证据，对候选 Skill 进行隔离测试、Baseline/Skill A/B 对照、相似任务迁移、无关任务回归、安全测试、按 Agent 独立准入、版本发布和回滚。第一版同时支持 Rook、Codex CLI 与 Claude Code CLI 作为被测对象。

候选 Skill 不因一次任务成功而直接进入正式能力库。它必须先进入隔离区，完成评测后才可在指定 Agent 上标记为 `promoted`。通过准入也不会静默修改用户真实的 Codex 或 Claude Code 配置；外部 Agent 的安装或导出必须是显式操作。

Rook 的技术描述为：

> An evaluation-gated adaptive agent harness that measures whether a Skill improves real task outcomes without causing regressions or unsafe behavior.

## 2. 问题定义

具备长期记忆和 Skill 自动生成能力的 Agent 面临四类常见风险：

1. **表面正确**：Skill 文本看起来合理，但真实执行没有帮助。
2. **过拟合**：Skill 只对产生它的原始任务有效，无法迁移到相似任务。
3. **负迁移**：Skill 在相关任务中有帮助，却误导无关任务。
4. **记忆污染**：未验证步骤、提示注入、密钥、临时状态或错误路径进入长期能力库。

普通单次 Benchmark 只能回答“Agent 这一次是否完成任务”。Rook 需要进一步回答：

- 同一个 Agent 在加载 Skill 前后是否发生可复现的能力变化；
- 变化来自 Skill 内容、Skill 路由，还是其他环境差异；
- Skill 是否能迁移到未见过的同类任务；
- Skill 是否导致回归、安全风险或额外成本；
- Agent、模型、Skill 或评测集变化后，原结论是否仍然有效。

## 3. 目标

Rook Agent EvalOps 必须满足以下目标：

1. 使用相同工作区快照和运行约束，完成无 Skill 与带 Skill 的配对实验。
2. 分离“Skill 内容是否有效”和“Agent 是否能正确路由 Skill”两个问题。
3. 支持直接任务、迁移任务、无关任务和安全任务四类评测。
4. 使用外部验证器判定结果，不接受被测 Agent 的完成自述作为成功证据。
5. 将 Codex CLI、Claude Code CLI 和 Rook 归一化为同一个运行与轨迹协议。
6. 保存原始事件和归一化轨迹，使评测结果可追溯、可复查。
7. 区分 Agent 能力失败、评测基础设施失败、认证失败和适配器失败。
8. 对每个 Agent、Agent 版本、模型和 Skill 版本独立作出准入决定。
9. 提供可配置、可解释的硬门槛与效果指标，不依赖不透明总分。
10. 支持候选隔离、版本历史、显式导出、状态失效和原子回滚。
11. 生成适合本地查看和 CI 消费的 Markdown 与 JSON 报告。
12. 在默认测试中不调用付费外部 Agent；真实 Codex/Claude 测试必须显式开启。

## 4. 非目标

第一版明确不做：

- 不训练或微调模型参数；
- 不实现强化学习或在线策略优化；
- 不做分布式任务队列或云端多租户平台；
- 不做 Web Dashboard；
- 不监控真实生产流量；
- 不自动修改 `~/.codex`、`~/.claude` 或其他用户全局配置；
- 不允许候选 Skill 绕过评测直接发布；
- 不以 LLM-as-a-Judge 作为唯一结果判定方式；
- 不在第一版支持任意第三方 Agent 插件市场；
- 不把不可比较的跨 Agent Token、费用或轨迹字段强行合并成统一绝对排名；
- 不继续旧设计中“任务成功后直接写入正式 Skill”的生命周期。

## 5. 核心原则

### 5.1 评测是发布边界

候选 Skill 的生成和正式发布必须分离：

```text
任务轨迹或人工导入
  -> SkillCandidate
  -> 安全与证据 Gate
  -> 隔离评测
  -> PromotionDecision
  -> 注册表
  -> 显式导出或启用
```

### 5.2 单一变量

同一 A/B Pair 的初始工作区、任务、Agent、模型、环境、工具权限、网络策略、预算和超时必须相同。唯一实验变量是候选 Skill 的处理方式。

### 5.3 外部验证优先

成功判定的优先级为：

1. 隐藏测试、退出码、API 或进程状态；
2. 文件状态、Git diff 和确定性工作区检查；
3. 工具轨迹规则；
4. LLM-as-a-Judge；
5. 人工复核。

被测 Agent 的最终回答只能提供解释，不能单独证明任务成功。

### 5.4 原始证据不可丢失

适配器必须先保存经过脱敏的原始事件，再进行归一化。未知事件可以保留但不能伪造语义；关键事件解析不完整时，评测必须 fail closed。

### 5.5 按 Agent 独立准入

同一 Skill 可以对 Rook 有效、对 Codex 无效、对 Claude Code 有害。准入状态必须绑定：

```text
agent_type + agent_version + model + skill_version + suite_version + policy_version
```

### 5.6 先隔离，后导出

外部 Agent 的候选 Skill 只注入实验工作区。`promoted` 表示“允许该目标 Agent 使用或导出”，不表示已经修改用户真实配置。

## 6. 总体架构

```text
                    +----------------------+
                    | Skill Candidate      |
                    | generated/imported   |
                    +----------+-----------+
                               |
                    +----------v-----------+
                    | Evolution Gate       |
                    | evidence/security    |
                    +----------+-----------+
                               |
                    +----------v-----------+
                    | EvalOps Runner       |
                    | paired orchestration |
                    +----+------------+----+
                         |            |
             +-----------v--+      +--v----------------+
             | Workspace    |      | Agent Adapters    |
             | Isolation    |      | Rook/Codex/Claude |
             +-----------+--+      +--+----------------+
                         |            |
                         +------++----+
                                ||
                    +-----------vv----------+
                    | Raw Events + State    |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    | Trace Normalization   |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    | Evaluators/ScoreCard  |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    | Promotion Policy      |
                    +-----+-----------+-----+
                          |           |
                    +-----v----+ +----v------+
                    | Registry | | Reports   |
                    +----------+ +-----------+
```

建议包边界：

```text
rook_agent/
  evolution/              # 候选生成、证据绑定和写入前 Gate
  evalops/
    models.py              # 统一领域模型和枚举
    suites.py              # EvalSuite/EvalCase 加载与校验
    workspace.py           # 快照、隔离工作区和清理
    runner.py              # A/B 编排、重复执行和取消
    adapters/
      base.py              # AgentAdapter 协议
      rook.py
      codex_cli.py
      claude_cli.py
    normalizers/
      codex.py
      claude.py
    evaluators/
      command.py
      file_state.py
      trajectory.py
      llm_judge.py
    scoring.py             # 指标、配对差异和置信度
    policy.py              # 硬门槛和准入决策
    registry.py            # 版本、状态、活动指针和回滚
    report.py              # scorecard.json 与 report.md
```

`rook_agent.evolution` 不再负责正式 Skill 发布。`rook_agent.evalops` 是候选 Skill 进入注册表的唯一边界。

## 7. 领域模型

### 7.1 AgentTarget

```python
@dataclass(frozen=True)
class AgentTarget:
    type: AgentType            # rook | codex | claude_code
    executable: str
    version: str
    model: str | None
    adapter_version: str
```

版本探测失败时不能创建有效 `AgentTarget`，运行标记为基础设施错误。

### 7.2 SkillBundle 与 SkillCandidate

`SkillBundle` 是 Agent 无关的规范表示：

```python
@dataclass(frozen=True)
class SkillBundle:
    name: str
    description: str
    triggers: tuple[str, ...]
    procedure: tuple[str, ...]
    verification: tuple[str, ...]
    pitfalls: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
```

`SkillCandidate` 增加版本、内容哈希、来源和隔离状态：

```python
@dataclass(frozen=True)
class SkillCandidate:
    bundle: SkillBundle
    version: int
    content_hash: str
    origin: CandidateOrigin
    status: CandidateStatus
```

Agent-specific Materializer 只能改变包装格式，不能改变 Skill 的语义内容：

```text
SkillBundle
  -> RookSkillMaterializer
  -> CodexSkillMaterializer
  -> ClaudeSkillMaterializer
```

### 7.3 EvalSuite 与 EvalCase

```python
@dataclass(frozen=True)
class EvalSuite:
    id: str
    version: str
    cases: tuple[EvalCase, ...]
    policy: PromotionPolicyConfig

@dataclass(frozen=True)
class EvalCase:
    id: str
    category: CaseCategory
    task: str
    fixture: Path
    evaluator: EvaluatorSpec
    timeout_seconds: int
    network_policy: NetworkPolicy
```

`CaseCategory` 至少包含：

```text
direct
transfer
regression
adversarial
```

### 7.4 RunSpec 与 AgentRun

`RunSpec` 固化所有影响实验的条件：

```python
@dataclass(frozen=True)
class RunSpec:
    experiment_id: str
    pair_id: str
    target: AgentTarget
    case: EvalCase
    treatment: Treatment
    workspace_snapshot_hash: str
    skill: SkillCandidate | None
    timeout_seconds: int
    turn_limit: int | None
    budget_limit: Decimal | None
    environment_allowlist: Mapping[str, str]
    permission_profile: str
```

`Treatment` 至少包含：

```text
baseline       # 不提供候选 Skill
forced_skill   # 明确加载候选 Skill，评测内容效果
routed_skill   # 候选存在但不显式指定，评测路由效果
```

`AgentRun` 记录运行状态、原始事件引用、工作区结果和可观测指标。缺失字段保持 `None`，不能伪造为零。

### 7.5 NormalizedTrace

统一事件类型至少包含：

```text
run_started
assistant_message
tool_requested
tool_completed
workspace_changed
verification_completed
run_completed
run_failed
```

每个归一化事件保留：

- 原始事件偏移或内容哈希；
- Agent 类型和版本；
- 事件时间与顺序；
- 结构化工具名、输入摘要、结果状态；
- 脱敏标记；
- normalizer 版本。

### 7.6 ScoreCard 与 PromotionDecision

`ScoreCard` 保存每个案例、每个 treatment 和总体聚合指标。它不提供不透明的单一总分。

`PromotionDecision` 至少包含：

```text
promoted
rejected
quarantined
stale
rolled_back
```

决定必须包含稳定 reason code、所用 policy 版本、输入 ScoreCard 哈希和创建时间。

## 8. Agent 适配器

### 8.1 协议

```python
class AgentAdapter(Protocol):
    def probe(self) -> AgentCapabilities: ...
    def prepare(self, spec: RunSpec, workspace: Path) -> PreparedRun: ...
    def run(self, prepared: PreparedRun) -> AgentRun: ...
    def cancel(self, run_id: str) -> None: ...
```

`probe()` 必须报告：

- 可执行文件解析路径；
- CLI 版本；
- 非交互模式是否可用；
- 结构化事件是否可用；
- 可配置的超时、预算、轮次和沙箱能力；
- Skill 注入模式；
- 适配器支持的事件覆盖率。

### 8.2 CodexCliAdapter

第一版使用 Codex 的非交互 `exec` 和 JSONL 事件能力。适配器负责：

- 指向隔离工作区；
- 使用临时/非持久会话；
- 固化模型、沙箱和配置覆盖；
- 捕获 stdout JSONL、stderr 和进程退出码；
- 禁止读取无关用户配置；
- 将候选 Skill 只物化到隔离环境；
- 记录精确 CLI 版本和实际参数的脱敏表示。

### 8.3 ClaudeCodeCliAdapter

第一版使用 Claude Code 的非交互 print 模式和 stream-json 输出。适配器负责：

- 禁止会话持久化和自动记忆污染；
- 固化 setting sources、工具范围、权限和预算；
- 捕获结构化事件、最终结果和进程退出码；
- 将候选 Skill 只物化到隔离环境；
- 记录精确 CLI 版本和实际参数的脱敏表示。

### 8.4 RookAdapter

RookAdapter 直接调用 Rook 的运行服务，但仍必须输出与外部 CLI 相同的 `AgentRun` 和 `NormalizedTrace`。不能为 Rook 使用更宽松的成功标准。

### 8.5 CLI 黑盒优先的原因

CLI 黑盒模式是第一版统一标准：

- 测量用户实际安装和运行的 Agent；
- Codex 与 Claude Code 接入方式对称；
- 框架不绑定某一家 SDK；
- CLI 版本可以成为评测结论的一部分。

SDK 接入可以在后续提供更丰富的中间事件，但不得改变评测协议和 ScoreCard 语义。

## 9. Skill 评测协议

### 9.1 内容效果与路由效果分离

每个候选 Skill 至少包含以下实验：

1. **Baseline**：不提供候选 Skill。
2. **Forced Skill**：明确加载候选 Skill，隔离内容本身的价值。
3. **Routed Skill**：候选存在但不明确指定，测量 Agent 是否正确发现和使用。

Forced Skill 没有改善时，不因 Routed Skill 成功命中而判定 Skill 有效。

### 9.2 四类任务

#### Direct

与产生 Skill 的任务族同类，但不能复用原始工作区状态。

#### Transfer

目标规律相同、表面参数不同，用于检测是否能迁移到未见任务。

#### Regression

与 Skill 无关的任务，用于检测错误触发和负迁移。

#### Adversarial

包含提示注入、伪造成功结果、敏感信息、越权请求、易变状态或误导性工具输出。

### 9.3 隔离和配对

每个 A/B Pair 从同一个不可变快照创建两个独立工作区：

```text
snapshot
  -> baseline-workspace
  -> candidate-workspace
```

工作区创建后记录初始哈希；运行结束后验证两组没有共享可写路径。隐藏验证器及答案必须位于被测 Agent 不可读取的位置。

### 9.4 两阶段成本控制

#### Fast Gate

Fast Gate 以少量 direct、transfer、regression 和 adversarial 案例快速淘汰无效或危险候选。任何安全失败直接拒绝。

#### Full Gate

Fast Gate 通过后才进入多案例、多次重复的 Full Gate。A/B 运行交替执行，聚合使用成功率、中位数、四分位区间和配对差值，不采用单次最好成绩。

### 9.5 外部验证

Evaluator 协议：

```python
class Evaluator(Protocol):
    def evaluate(
        self,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult: ...
```

Evaluator 运行在独立边界中。被测 Agent 无法修改 evaluator 或隐藏测试。

## 10. 指标

### 10.1 结果指标

- 任务成功率；
- 外部验证通过率；
- 首次尝试成功率；
- 超时、轮次耗尽和预算耗尽率。

### 10.2 轨迹指标

- 工具调用数量；
- 失败工具调用数量；
- 重复或无效工具调用；
- 是否执行必要验证；
- 权限拒绝和危险动作；
- 轨迹完整度。

### 10.3 效率指标

- Token（仅在实际可观测时）；
- API 费用（仅在实际可观测时）；
- 端到端耗时；
- 恢复次数；
- 工具调用减少比例。

### 10.4 学习与路由指标

- Positive Transfer；
- Negative Transfer；
- Skill Reuse Success Rate；
- Routing Precision；
- Routing Recall；
- Skill Precision；
- Contamination Rate；
- Regression Rate。

跨 Agent 默认比较各自相对基线增益。只有单位、口径和观测范围一致的指标才允许绝对横向比较。

## 11. ScoreCard 与准入策略

### 11.1 硬门槛

以下条件不能被其他收益抵消：

- 安全违规数量为零；
- 敏感信息持久化数量为零；
- 没有新增回归失败；
- 有效配对样本达到 policy 最低要求；
- trace coverage 达到最低要求；
- 外部 evaluator 正常完成；
- 没有未解释的越权或隐藏测试泄漏。

### 11.2 效果门槛

默认 policy 允许两种通过路径：

1. 成功率达到配置的最小提升，且无硬门槛失败；
2. 成功率不下降，且工具调用、Token 或耗时至少一项达到配置的中位改善幅度。

示例 policy：

```toml
[requirements]
min_valid_pairs = 6
max_safety_failures = 0
max_new_regression_failures = 0
min_trace_coverage = 0.95

[effect]
min_success_uplift = 0.10
min_efficiency_improvement = 0.15

[routing]
min_precision = 0.80
min_recall = 0.80
```

阈值由 EvalSuite 版本化配置，不硬编码为所有任务的永恒标准。

### 11.3 基础设施结果不污染能力统计

以下结果不进入 Skill 成败分母：

```text
ADAPTER_UNAVAILABLE
AUTH_FAILED
VERSION_UNSUPPORTED
INFRA_ERROR
ADAPTER_ERROR
USER_CANCELLED
```

以下结果属于规定约束下的 Agent 结果，可以进入能力统计：

```text
WRONG_RESULT
VERIFICATION_FAILED
TIMEOUT
TURN_LIMIT
BUDGET_EXHAUSTED
UNSAFE_ACTION
```

基础设施错误过多会使本次实验无效，而不是给候选 Skill 一个虚假的低分或高分。

## 12. 存储

### 12.1 版本化定义

评测定义进入 Git：

```text
evals/
  suites/<suite>/suite.toml
  cases/<case>/task.md
  cases/<case>/fixture/
  cases/<case>/evaluator.py
  policies/default.toml
```

### 12.2 运行产物

运行数据保存在 Git ignored 的 `.rook`：

```text
.rook/eval-runs/<experiment-id>/
  manifest.json
  baseline/
  candidate/
  raw-events/
  normalized-traces/
  scorecard.json
  report.md
```

### 12.3 Skill 注册表

```text
.rook/skill-registry/<skill>/
  candidates/<version>/
  promoted/
  history/
  registry.json
```

原始事件、ScoreCard 和准入决定采用内容哈希互相引用。已完成实验不可原地覆盖；重跑生成新的 experiment id。

## 13. 发布、导出、失效和回滚

### 13.1 按 Agent 活动指针

```json
{
  "skill": "windows-cmd-switching",
  "active_versions": {
    "rook": 2,
    "codex": 1,
    "claude_code": null
  }
}
```

发布采用原子指针切换，旧版本和历史决定不删除。

### 13.2 显式导出

对外部 Agent 的 `promoted` 只表示具备导出资格。用户显式执行 export 后，Materializer 才生成目标格式。默认导出到用户指定目录，不静默写入全局配置。

### 13.3 失效

以下变化使既有准入状态变为 `stale`：

- Agent CLI 版本变化；
- 模型变化；
- Skill 内容变化；
- EvalSuite、Evaluator 或 policy 变化；
- 工具范围或权限策略变化；
- 关键 normalizer 版本变化。

### 13.4 回滚

后续评测发现回归时，注册表原子切回最近一个仍有效的版本，并将当前版本标记为 `rolled_back`。回滚原因和触发实验必须可追溯。

## 14. CLI 体验

第一版提供：

```powershell
rook eval doctor

rook eval run `
  --skill .rook\skill-registry\example\candidates\1 `
  --suite evals\suites\windows-shell `
  --agents rook,codex,claude

rook eval report <experiment-id>
rook skill status <skill-name>
rook skill rollback <skill-name> --agent codex --to-version 1
rook skill export <skill-name> --agent claude --output <directory>
```

`eval doctor` 负责检查可执行文件、版本、认证、结构化输出、隔离工作区和可选功能。Doctor 失败不修改用户配置。

## 15. 安全与隐私

1. 所有被测 Agent 默认在隔离工作区运行。
2. 环境变量采用显式 allowlist，不继承无关凭据。
3. 外部网络默认关闭；确需网络的 EvalCase 必须声明。
4. 原始 CLI 输出在持久化前脱敏。
5. 安全 Gate 和报告只记录稳定 reason code、哈希和计数，不记录匹配到的秘密原文。
6. 隐藏 evaluator、答案和对照工作区对 Agent 不可读。
7. 候选 Skill 不能自行声明额外权限。
8. 任何 `--dangerous` 或跳过权限的模式只能在外层已验证隔离环境中显式启用，第一版默认不使用。
9. 自动发布只影响 Rook 本地注册表；写入外部 Agent 配置必须显式导出。

## 16. 异常处理

### 16.1 CLI 或认证不可用

标记为基础设施错误，不计入候选 Skill 成败。Experiment 保留诊断信息并保持不可准入状态。

### 16.2 超时、预算或轮次耗尽

若评测约束已成功建立，这些属于 Agent 在规定资源内的结果，进入 ScoreCard。

### 16.3 Schema 漂移

未知事件保留在 raw events 中。关键工具调用、结果或终止事件无法解析时，标记 `TRACE_INCOMPLETE` 并禁止准入。

### 16.4 取消

取消必须终止完整子进程树，尤其覆盖 Windows PowerShell、Node 包装脚本和真实 CLI 子进程。部分产物保留用于诊断，但不进入 A/B 统计。

### 16.5 清理失败

清理失败不删除证据；工作区标记为需要人工清理。后续实验不得复用状态不明的工作区。

## 17. 测试策略

### 17.1 Adapter 契约测试

使用录制并脱敏的 JSONL fixtures 覆盖：

- 正常完成；
- 工具失败；
- 权限拒绝；
- 超时和取消；
- stderr 噪声；
- JSONL 截断；
- 未知事件；
- 不支持的 CLI 版本。

### 17.2 工作区隔离测试

验证：

- A/B 初始哈希一致；
- 两组写入互不影响；
- 原始 snapshot 不变；
- 隐藏 evaluator 不可读；
- 用户真实 Codex/Claude 配置不被修改；
- 失败和取消后没有可复用脏状态。

### 17.3 ScoreCard 金标测试

使用确定性数据覆盖：

- 有效提升得到 `promoted`；
- 新增回归得到 `rejected`；
- 样本不足得到 `quarantined`；
- 安全失败不能被效率收益抵消；
- 缺失 Token 不被当作零；
- 基础设施错误不污染能力分母；
- Agent 或 suite 版本变化使状态变为 `stale`。

### 17.4 端到端确定性测试

默认使用 Fake Agent 完整覆盖：

```text
candidate
  -> paired workspaces
  -> runs
  -> raw events
  -> normalized traces
  -> evaluators
  -> scorecard
  -> decision
  -> registry
  -> rollback
```

### 17.5 外部 Agent 冒烟测试

真实 Codex 和 Claude Code 测试通过显式环境变量开启，默认测试不产生 API 成本：

```powershell
$env:ROOK_RUN_EXTERNAL_EVALS = '1'
pytest tests/test_evalops_external_smoke.py
```

## 18. 与现有 Rook Forge 工作的关系

现有 Forge Task 1-3 的可复用部分包括：

- evolution 配置与领域模型；
- append-only 审计事件；
- TaskTraceBuilder；
- EvidenceClassifier；
- Secret/Volatility/Injection/Scope 等 Gate 基础。

它们转为候选生成和评测准入之前的证据治理层。现有 Task 3 仍有未完成的审查修复，不能视为已完成或直接作为安全保证。

旧 Forge 实施计划中 Task 4 及之后的“生成 Skill 后直接发现、写入、路由和记录使用结果”不再继续执行。新计划必须以本设计为准，重新安排：

1. 统一领域模型与候选状态；
2. 隔离工作区和 AgentAdapter；
3. 事件归一化与外部验证器；
4. A/B Runner 与 ScoreCard；
5. PromotionPolicy、Registry 和 Report；
6. evolution 候选生成接入；
7. Codex/Claude 真实冒烟测试。

## 19. MVP 验收标准

第一版完成必须同时满足：

1. 一个候选 Skill 能针对 Rook、Codex CLI 和 Claude Code CLI 创建隔离实验。
2. Baseline 与 Forced Skill 从同一 workspace snapshot 开始。
3. Routed Skill 能单独报告路由 precision/recall，而不与内容效果混淆。
4. 至少有 direct、transfer、regression 和 adversarial 四类案例。
5. 成功结果由外部 evaluator 证明。
6. 三个 Agent 的原始事件被保存并归一化为统一轨迹。
7. 基础设施错误不被误算为 Skill 失败。
8. ScoreCard 清楚展示原始指标、相对变化、缺失字段和样本数。
9. 安全失败或新增回归必然阻止 promotion。
10. 同一 Skill 可以对不同 Agent 产生不同准入状态。
11. Agent、模型、Skill、suite 或 policy 变化能使旧结论变为 stale。
12. promotion 不修改用户真实 Codex/Claude 配置。
13. rollback 可以原子恢复旧活动版本并保留完整历史。
14. 默认测试使用 Fake Agent，不调用付费外部 Agent。
15. 可选真实冒烟测试能分别运行本机 Codex CLI 和 Claude Code CLI。

## 20. 简历安全表述

完成 MVP 后可表述为：

> 设计并实现面向自适应 LLM Agent 的 EvalOps 框架，以执行结果和工具轨迹为依据，对自动生成 Skill 进行隔离测试、基线/学习后 A/B 对照、迁移评测、安全回归和按 Agent 独立准入；通过统一 CLI Adapter 接入 Rook、Codex 与 Claude Code，并以版本化 ScoreCard 控制 Skill 发布、失效和回滚。

不得宣称：

- 修改了模型权重；
- 实现了通用自主进化；
- 全面优于 Claude Code 或 Codex；
- 尚未运行的跨 Agent 指标已经取得提升；
- LLM Judge 可以替代确定性验证。

Rook 的差异化不在于生成代码能力超过成熟 Coding Agent，而在于为带有长期记忆和 Skill 的工具型 Agent 提供可验证、可比较、可审计的能力演化发布边界。

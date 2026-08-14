# Memory A/B v1 Pilot 报告

## 结论

首轮 4-pair Pilot 与修复后的 4-pair Pilot 均于 2026-08-01 使用
`deepseek/deepseek-v4-flash` 完成。修复后，非空 Patch 从 1/8 增加到 4/8，Baseline 与
Memory 均首次获得 1/4 成功；两臂成功率同为 25%，配对成功率提升仍为 0。

这证明统一执行契约和提前纠偏减少了空 Patch，并产生了真实能力结果；当前项目记忆仍未证明
可以提升配对成功率或降低重复失败。20-pair Formal 继续暂停。

## 修复后 4-pair Pilot

| 指标 | 结果 |
|---|---:|
| 完整配对 | 4/4 |
| Baseline 成功率 | 1/4（25%） |
| Memory 成功率 | 1/4（25%） |
| 配对成功率提升 | 0pp |
| 新增回归 | 0 |
| 有效配对 Provider 请求 | 96 |
| 有效配对 Token | 1,268,816 |
| 授权窗口实际 Provider 请求 | 110/192 |
| 授权窗口实际 Token | 1,391,785 |
| Memory 相对 Baseline 中位 Token 变化 | +39,881 |
| Memory 相对 Baseline 中位时延变化 | -35.71 秒 |
| Memory 相对 Baseline 中位 Tool Call 变化 | +1 |
| 有效实验臂非空 Patch | 4/8 |
| 轨迹完整度 | 10/10（100%，含重试） |
| 初始工作区哈希一致 | 4/4 |
| 实验指纹一致 | 4/4 |
| 基础设施重试 | 1 |
| 重复基础设施失败 | 0 |
| 秘密泄漏 | 0 |
| stale / revoked / unconfirmed 加载 | 0 / 0 / 0 |

时延中位数下降没有伴随成功率提升，Token 中位数增加。Pilot 只有 4 个配对，正式 ScoreCard
按预期返回 `incomplete_pairs`，`resume_claim_allowed=false`。

### 修复后逐任务结果

| 任务 | Baseline | Memory | Baseline Token | Memory Token | Baseline 秒 | Memory 秒 | Tool Call B/M |
|---|---|---|---:|---:|---:|---:|---:|
| `django__django-13220` | `validation_failed / hidden_patch_conflict` | `validation_failed / hidden_patch_conflict` | 127,651 | 148,228 | 535.5 | 311.5 | 18 / 15 |
| `pylint-dev__pylint-7114` | `validation_failed / agent_patch_empty` | `validation_failed / agent_patch_empty` | 136,242 | 195,427 | 171.4 | 341.1 | 15 / 15 |
| `pydata__xarray-3364` | `validation_failed / agent_patch_empty` | `validation_failed / agent_patch_empty` | 156,747 | 225,421 | 205.0 | 243.7 | 16 / 18 |
| `astropy__astropy-12907` | `passed / hidden_and_regression_passed` | `passed / hidden_and_regression_passed` | 140,147 | 138,953 | 265.2 | 155.0 | 13 / 15 |

Astropy Baseline 首次运行在 2 次请求后出现 `provider_network_error`。执行器从干净工作区重试
整对，Baseline 与 Memory 的 `r1` 均通过；ScoreCard 仅聚合完整的 `r1/r1` 配对。一次网络
错误和整对重试额外产生 14 次请求，未出现第二次基础设施错误。

### 与首轮对比

| 指标 | 首轮 | 修复后 |
|---|---:|---:|
| 非空 Patch | 1/8 | 4/8 |
| Baseline 成功率 | 0% | 25% |
| Memory 成功率 | 0% | 25% |
| 配对成功率提升 | 0pp | 0pp |
| 新增回归 | 0 | 0 |

Pylint Memory 臂在最后一次无工具请求中才输出编辑调用文本，编辑没有进入执行器；Xarray 两臂
继续把请求预算耗在复现、检索和大范围方案设计。当前提前纠偏对部分任务有效，后续需要将预算
阶段显式化，并在仍可调用工具的边界强制要求最小 Patch 与 `git diff`，再进行新的小规模复测。

## 首轮核心指标

| 指标 | 结果 |
|---|---:|
| 完整配对 | 4/4 |
| Baseline 成功率 | 0/4（0%） |
| Memory 成功率 | 0/4（0%） |
| 配对成功率提升 | 0pp |
| 新增回归 | 0 |
| 有效 Pilot Provider 请求 | 96 |
| 有效 Pilot Token | 1,074,835 |
| Memory 相对 Baseline 中位 Token 变化 | -5,665.5 |
| Memory 相对 Baseline 中位时延变化 | -80.15 秒 |
| 轨迹完整度 | 8/8（100%） |
| 初始工作区哈希一致 | 4/4 |
| 实验指纹一致 | 4/4 |
| 基础设施重试 | 0 |
| 秘密泄漏 | 0 |
| stale / revoked / unconfirmed 加载 | 0 / 0 / 0 |

时延和 Token 下降没有伴随任务成功，不能表述为有效效率提升。Pilot 只有 4 个配对，正式
ScoreCard 按预期返回 `incomplete_pairs`，`resume_claim_allowed=false`。

## 首轮逐任务结果

| 任务 | Baseline | Memory | Baseline Token | Memory Token | Baseline 秒 | Memory 秒 | Tool Call B/M |
|---|---|---|---:|---:|---:|---:|---:|
| `django__django-13220` | `validation_failed / agent_patch_empty` | `validation_failed / hidden_patch_conflict` | 147,807 | 106,568 | 345.3 | 271.3 | 18 / 14 |
| `pylint-dev__pylint-7114` | `validation_failed / agent_patch_empty` | `validation_failed / agent_patch_empty` | 124,962 | 111,504 | 195.1 | 54.5 | 15 / 18 |
| `pydata__xarray-3364` | `validation_failed / agent_patch_empty` | `validation_failed / agent_patch_empty` | 144,972 | 147,099 | 150.1 | 63.8 | 17 / 15 |
| `astropy__astropy-12907` | `validation_failed / agent_patch_empty` | `validation_failed / agent_patch_empty` | 140,894 | 151,029 | 88.9 | 134.8 | 13 / 15 |

7/8 实验臂没有产生可验证 Patch；另 1 臂的 Patch 与隐藏测试补丁冲突。下一轮应优先分析
Agent 为什么在 Tool 预算耗尽后留下空 Patch，以及隐藏补丁冲突对应的公开问题语义偏差。

## `agent_patch_empty` 根因复盘

8 个实验臂都用满 12 次 Provider 请求，共发起 125 次 Tool Call。7 个空 Patch 实验臂从未
调用 `edit` 或 `write`。Astropy Baseline 在第 4 次请求已定位 `_cstack` 的错误赋值，在第
6 次请求给出准确最小修法，随后第 6–11 次请求全部用于探测错误的 Python 环境，最终在保留
的第 12 次无工具请求中结束。

确定性根因包括：

- Memory A/B 的可见任务提示遗漏 Native 已有的固定 testbed Python、首次修改截止点和
  `git diff` 收尾约束；
- Native 工具表暴露宿主机 `diagnostics`，Django 轨迹因此在 Linux 容器任务中生成并执行
  PowerShell 命令；
- 8 条轨迹共收到 30 次 Todo 相关控制提醒，进一步挤占了 12 次请求内的实现注意力；
- 运行时直到最后一次请求才禁用工具收尾，缺少“已定位修法但工作区仍为空”的提前纠偏。

对应修复已完成：Memory 与 Native 共用同一执行契约；Native Session 显式使用 Linux
`/bin/sh` 和固定 testbed Python；宿主 `diagnostics` 与 Todo 从有界评测工具表移除；第 8
次请求前工作区仍为空时，运行时只注入一次最小修改提醒。最后一次无工具收尾边界保持不变。

离线验证结果：新增 3 个根因回归测试通过；Native、Memory、Agent Loop 与 Prompt 专项
`120 passed`；Benchmark、Memory、Native、Recovery 与 Seed 专项 `89 passed`；Ruff、
本轮四个独立模块的 mypy、编译检查和 `git diff --check` 通过。新的真实 4-pair Pilot
必须单独授权，只有出现非空 Patch 增加且至少一个任务获得有效能力改善，才考虑 Formal。

## 证据与预算

修复后复测：

- Experiment ID：`memory-pilot-20260801T094612681505Z-b24cffd2`；
- 4/4 配对完整，10 条运行记录轨迹完整；
- 有效 ScoreCard 使用 96 次请求；一次基础设施失败及整对重试后，授权窗口实际使用
  110/192 次请求；
- Catalog、Validator manifest 与 Tool Schema 指纹和首轮保持一致；
- Provider/模型固定为 `deepseek/deepseek-v4-flash`，没有回退。

首轮：

- Experiment ID：`memory-pilot-20260801T080919703120Z-a04a20c5`；
- Catalog fingerprint：`33a5692e5a50b900eeb4de5f2856b52ac628d7f82bfa013618f1a2d099a2e225`；
- Validator manifest fingerprint：`3382235b9db99bd21abbb96e4f55a81c2719467488422d5b836933180634cafc`；
- Tool Schema fingerprint：`1764bd818dab06a14336505173a12050`；
- Provider/模型固定为 `deepseek/deepseek-v4-flash`，没有回退；
- 有效轮 8 个实验臂各使用 12 次 Provider 请求，共 96 次；
- 授权窗口内另有一次确定性泄漏检测误报诊断，使用 12 次请求且未进入 Pilot ScoreCard；
- 授权窗口累计使用 108/192 次 Provider 请求。

误报由通用字符串 `python` 和仓库公开文件 `runtests.py` 触发。检测规则已收敛为私有补丁
完整路径、完整 Validator 命令及冻结指纹，并增加回归测试。容器 Git `dubious ownership`
也已通过容器专用全局配置修复，保持非 root、禁网和只读根文件系统边界。

全部模型输出、Patch、验证结果和标准化轨迹保存在本地私有实验制品中；公开报告不包含
隐藏 Validator、私有补丁内容、API Key 或消息正文。

## 定向 2-pair 复测准备

有界评测 Agent Loop 已加入确定性阶段预算：前 5 次请求用于定位，随后只暴露最小读取与
编辑工具；仍无 Patch 时进入强制修改阶段；形成 Patch 后依次只暴露 `git_diff` 与容器
`shell`，验证失败后才开放一次有界修正。最终无工具收尾请求不再携带 Tool Schema，避免
模型输出无法执行的编辑调用文本。阶段策略写入 `agent_policy` 指纹，保证两臂可比。

CLI 已支持仅在 Pilot 中通过两个 `--task` 参数选择精确的 2-pair 子集。当前冻结目标为
`pylint-dev__pylint-7114` 与 `pydata__xarray-3364`，对应两条不同的 active Memory；私有
仓库源、密封 Validator 和空制品目录 readiness 均已通过。

离线验证覆盖阶段切换、越阶段工具拒绝、验证失败后的有界修正、定向任务选择及 Formal
拒绝子集运行，共 `44 passed`；Ruff、相关模块 mypy、编译检查与 `git diff --check`
通过。真实 2-pair Pilot 仍需独立费用授权，正常预算为 48 次 Provider 请求；每个完整配对
最多允许一次干净重试，授权硬上限为 96 次，重复基础设施失败立即停止。

### 2026-08-01 真实复测停止记录

- Experiment ID：`memory-pilot-20260801T123039644219Z-847624b3`；
- 目标：Pylint/Xarray 2-pair，授权硬上限 96 次 Provider 请求；
- 实际运行：Pylint Baseline/Memory 首轮及 Baseline 干净重试，共 3 次 Provider 请求；
- 三次请求均在鉴权阶段返回 `provider_auth_error`，Token 观测值为 0；
- 重复基础设施失败触发后立即终止，Xarray 与 Pylint Memory 重试均未启动；
- 部分 ScoreCard 为 `incomplete_pairs`，`valid=false`，`resume_claim_allowed=false`；
- 本轮只证明停止策略生效，不能形成 Memory 效果结论。

### 凭据轮换后的网络失败记录

- Experiment ID：`memory-pilot-20260801T125448470347Z-a74e62f5`；
- 新凭据、Provider、模型、Docker 与空制品根目录 readiness 全部通过；
- Pylint 首轮 Baseline/Memory 均返回 `provider_network_error`；
- 干净重试的 Baseline 再次返回同类错误，触发重复基础设施失败停止条件；
- Memory 重试在并行停止传播前完成 4 次请求，合计可审计 Provider 请求 9/96；
- 成功返回过部分模型响应，终态 Token 未落盘，因此 Token 标记为 `not observed`；
- Xarray 未启动，完整配对为 0，部分 ScoreCard 仍为 `incomplete_pairs`；
- 停止后 DNS、TCP 443 与无凭据 HTTPS 探针均连通，HTTPS 返回预期 401，说明探针时刻的
  基础网络可达；本轮无法区分瞬态连接故障与 Provider 请求链路故障，不能形成 Memory 效果结论。

### 单次 Provider readiness

- 使用轮换后的 Keyring 凭据调用 `deepseek/deepseek-v4-flash` 恰好 1 次；
- 请求不携带工具，SDK 重试为 0，最大输出为 8 Token；
- Provider 与模型回传一致，输入 87 Token、输出 8 Token、总计 95 Token，时延 3,133 ms；
- 返回 `finish_reason=length` 且正文为空，说明 8 Token 预算不足以形成完整可见回复；
- readiness 仅通过鉴权、模型与连接检查，完整响应检查失败，因此未启动新的 2-pair Pilot。

第二次 readiness 使用 128 Token 上限、无工具且 SDK 重试为 0。唯一一次模型请求在读取
响应头时失败，底层错误为 `SSLV3_ALERT_BAD_RECORD_MAC`，归一化为
`ProviderErrorKind.NETWORK_ERROR`。Windows Internet Settings 启用了本地代理
`127.0.0.1:10808`，httpx 自动通过该代理建立 TLS；环境变量代理与 WinHTTP 代理均为空。
使用 `trust_env=False` 的直连 HEAD，以及设置 `NO_PROXY=api.deepseek.com` 后的默认 httpx
HEAD，均在 0.6 秒内稳定返回预期 401，确认 DeepSeek 直连 TLS 正常。第二次 readiness
仍未形成完整模型响应，下一次调用必须显式绕过该本地代理，并继续使用单次、零重试边界。

随后为 OpenAI-compatible Provider 增加显式 `trust_env` 配置，并在当前 DeepSeek 配置中设为
`false`。Provider 构造检查确认 `deepseek/deepseek-v4-flash` 使用直连且 SDK 重试为 0。修复后的
第三次 readiness 恰好发起 1 次无工具、128 Token 请求，`provider.complete()` 已返回
`ChatResponse`，证明直连 TLS、Keyring 鉴权、模型端点和响应解析链路均可用。验证脚本随后误读
不存在的 `ChatResponse.message` 字段而在本地退出，因此该次响应的正文、Token 与时延未形成
可靠记录；本次只据此判定 DeepSeek 连接恢复，不形成 Memory Pilot 效果结论，也未追加重试。

### DeepSeek 工具调用兼容性停止记录

- 长路径预检 Experiment ID：`memory-pilot-20260801T135552594693Z-4fc62305`；Windows Git
  在 221 字符目标路径报 `'$GIT_DIR' too big`，Provider 调用为 0。短路径克隆探针随后在同一
  base commit 上通过，正式制品根切换到 `D:\RMP3_1`；
- 定向复测 Experiment ID：`memory-pilot-20260801T140054314963Z-d7f509cc`；
- Pylint Baseline/Memory 首轮均在第 6 次 Provider 请求返回 `provider_api_error`；两臂此前
  5 次请求均成功，失败边界与阶段预算第一次把 `tool_choice` 切为 `required` 完全一致；
- 干净配对重试的两臂均在第 1 次请求返回 `provider_network_error`，重复基础设施失败停止条件
  生效，Xarray 没有启动；
- 可审计调用共 14 次，观测 Token 共 75,403，轨迹完整 4/4，非空 Patch 0，完整配对 0；
  因此成功率、Validator、Tool Call、Token 与时延均不能用于 Memory 效果比较。

代码审计发现两个 DeepSeek 思考工具调用兼容性缺口：DeepSeek preset 错误声明支持
`tool_choice`；会话虽然保存了响应中的 `reasoning_content`，ContextBuilder 和请求序列化却
没有回放它。修复后，Rook 仍用 `required` 在本地验证阶段行为，DeepSeek 线上请求完全省略
`tool_choice`；DeepSeek 工具调用历史同时携带原始 `reasoning_content`。这与官方 V4 兼容表中
`supportsToolChoice=false`、`requiresReasoningContentForToolCalls=true` 的定义一致。

离线验证结果：新增 4 个兼容性回归测试先红后绿；Provider、ContextBuilder、Native Runtime、
配置与 Memory Runtime 专项共 `78 passed`；Ruff 与 5 个直接影响模块的 mypy 全部通过。新的
2-pair Pilot 必须从干净工作区重新开始并重新授权；当前没有 Memory 正向、负向或中性效果结论。

新的不可变 readiness 制品为 `targeted-pilot-v4-readiness.json`。它固定了 Provider 配置、8 个
直接影响模块、Catalog、Validator、Tool Schema、两个 base commit、两个容器镜像及请求预算；
下一轮制品根固定为运行前不存在的 `D:\RMP4_1`。私有 Catalog 与密封 Validator 重新验证通过，
30-task Formal 继续关闭。

### v4 定向复测停止记录

- Experiment ID：`memory-pilot-20260801T145816822061Z-730c5cd3`；
- Readiness SHA-256：`e982806da1201bde4a43d745c5000d482738573e26bf54c614d60ce37e6f2e4a`；
- Pylint Baseline 首轮在 5 次请求后返回 `provider_network_error`；Memory 首轮完成 6 次请求，
  没有复现此前第 6 次请求的 Provider 400，随后因阶段预算拒绝越阶段 `shell` 而以
  `agent_patch_empty` 结束；
- 干净整对重试中，Baseline 第 1 次请求再次返回 `provider_network_error`，Memory 在已调度的
  5 次请求后也返回同类错误；服务写入 `repeated_pair_infrastructure_failure` 终态并停止，Xarray
  没有启动；
- 共使用 17 次 Provider 请求、97,559 Token、23 次 Tool Call/执行，累计时延 159,806 ms；
- 4/4 轨迹完整、4/4 容器清理完成、重复工具失败尝试 0、非空 Patch 0、完整配对 0；
- ScoreCard 为 `incomplete_pairs`、`valid=false`、`resume_claim_allowed=false`，所有配对差值
  保持 `null/not observed`。

不可变结果制品为 `targeted-pilot-v4-result.json`，SHA-256 为
`b6d3c3ca09a765c2eb5abc48f21213678fa8a34057a86173831046a23be3c354`。本轮证明 DeepSeek
工具协议修复越过了原有第 6 请求 400 边界，同时再次触发网络基础设施停止条件；它没有形成
Memory 效果证据。4-pair 扩展关闭，30-task Formal 继续暂停。

### v5 基础设施诊断与定向复测停止记录

Native 与 Memory 执行器现将基础设施错误写入 `validation.json` 和
`runtime-manifest.json`。字段包含最深层异常类型和最多 2,000 字符的脱敏消息；两份制品必须
一致。新增两个回归测试按 RED → GREEN 完成，Native/Memory 直接专项 `26 passed`，相关
Benchmark 回归 `68 passed`，Ruff 规则检查与 mypy 通过。

- Readiness SHA-256：`0443629a592363461a7d1cbff0e1ca57aadadcc7046a5971b3f6f2e94a80dda5`；
- Experiment ID：`memory-pilot-20260801T154626752289Z-088635e4`；
- Pylint Baseline/Memory 首轮及干净整对重试的四个实验臂均在第 1 次 Provider 请求返回
  `provider_network_error`；
- 四份错误制品一致记录底层异常 `socket.gaierror` 与消息
  `[Errno 11001] getaddrinfo failed`，秘密模式扫描命中 0；
- 停止条件 `repeated_pair_infrastructure_failure` 生效，Xarray 没有启动；
- 共调度 4 次 Provider 请求，观测 Token 0，Tool Call/执行 0，非空 Patch 0，累计时延
  49,846 ms；
- 4/4 轨迹完整、4/4 容器清理完成、完整配对 0；
- 停止后的只读探针重新解析 `api.deepseek.com` 并成功连接 TCP 443，证据支持瞬态宿主 DNS
  故障解释；本轮仍不能形成 Memory 效果结论。

不可变结果制品为 `targeted-pilot-v5-result.json`，SHA-256 为
`719349293fdabcefaf43c802264d287210b6780b3270b95925c628839031f5e0`。ScoreCard 保持
`incomplete_pairs`，4-pair 扩展关闭，30-task Formal 继续暂停。

### v6 付费运行前网络门禁

`rook benchmark memory run` 现在会在创建 Provider、实验目录和 Session 之前连续执行三轮
DNS 与 TCP readiness。每轮都重新解析 Provider 主机并新建 TCP 连接；任意一轮失败都立即终止，
模型调用保持 0。门禁固定使用 5 秒连接超时和 250 ms 轮间隔。

新增三个测试按 RED → GREEN 完成，相关 Benchmark 回归 `71 passed`，Ruff、格式检查和 mypy
通过。真实零模型探针已对 `api.deepseek.com:443` 连续通过 3/3。新的不可变 readiness 制品为
`targeted-pilot-v6-readiness.json`，SHA-256 为
`ee032db0ecb1286128b2041e58c44ba41e7a8c9be4a32a3ee42e813bcd1579ee`；下一轮制品根固定为
运行前不存在的 `D:\RMP7_1`，仍需独立付费授权后才能启动。

### v6 定向 2-pair 完整结果

- Experiment ID：`memory-pilot-20260801T172409686948Z-9041c570`；
- 运行前网络门禁连续通过 3/3，两个任务、四个实验臂全部形成能力终态，基础设施错误 0；
- 共使用 25 次 Provider 请求、249,483 Token、32 次 Tool Call/执行，累计时延 421,148 ms；
- 4/4 轨迹完整、4/4 容器清理完成、秘密模式命中 0、负控制记忆加载 0；
- Pylint 与 Xarray 的 Baseline/Memory 四臂均以 `agent_patch_empty` 结束，非空 Patch 0；
- 两臂成功率均为 0%，配对提升 0；Memory 相对 Baseline 的中位 Token 增量为 25,651.5，
  中位时延增量为 29,443 ms；
- ScoreCard 因正式门槛要求 20 个完整配对而保持 `valid=false/incomplete_pairs`，当前 2 个配对
  只用于定向 Pilot 诊断。

四条轨迹呈现相同确定性终止模式：阶段预算已切入 edit-only，但模型继续请求未暴露的
`shell` 或 `grep`，本地预算验证立即结束该 Turn，导致工作区没有机会获得一次纠正后的最终编辑
请求。下一步应把该协议错误返回为有界纠正结果，并只允许一次最终 edit-only Provider 请求；
修复前不扩展 4-pair，也不启动 Formal。

不可变结果制品为 `targeted-pilot-v6-result.json`，SHA-256 为
`25a3165145b61f446e457a4aef5820d583ef3cc6570e127786cfecfc5b14934e`。

### v7 未暴露工具有界纠正

运行时现已把阶段预算中的未暴露工具调用作为可纠正协议结果处理：违规调用不会进入执行器，
Agent Loop 会保存脱敏纠正证据，并提供恰好一次只暴露 `edit`、`write`、`apply_patch` 的
Provider 请求。第二次仍请求未暴露工具时立即结束当前 Turn，不形成第三次调用。

新增测试分别覆盖“单次纠正后形成非空 Patch”与“连续两次违规后有界终止”。Agent Loop 与
Native 专项共 `100 passed`；Native、Memory、CLI 和 readiness 组合回归共 `49 passed`；
Ruff、Native Runtime mypy 与 `git diff --check` 通过。Memory 目录和私有 Validator 重新离线
验证通过，目录指纹仍为 `33a5692e5a50b900eeb4de5f2856b52ac628d7f82bfa013618f1a2d099a2e225`，
Validator 指纹仍为 `3382235b9db99bd21abbb96e4f55a81c2719467488422d5b836933180634cafc`。

下一轮继续只选择 `pylint-dev__pylint-7114` 与 `pydata__xarray-3364`，新的制品根固定为
运行前不存在的 `D:\RMP8_1`，Provider 请求总上限保持 96。该轮只检查四个实验臂能否形成
非空 Patch 和 Validator 能力结果；满足这两个条件前，4-pair 扩展与 Formal 保持暂停。

### v7 定向 2-pair 完整结果

- 首次预检在 Provider 调用前发现 Docker Desktop daemon 未运行，Provider 请求为 0；失败目录已
  独立归档。启动 Docker Desktop 29.0.1 后，从重新创建的 `D:\RMP8_1` 完整重跑；
- Experiment ID：`memory-pilot-20260802T090000912447Z-577a37a8`；运行前网络门禁通过 3/3，
  两个任务、四个实验臂全部形成终态，基础设施错误 0；
- 共使用 42 次 Provider 请求、1,062,772 Token、48 次 Tool Call、46 次实际 Tool 执行，
  累计实验臂时延 2,000,768 ms；授权上限为 96 次请求；
- 4/4 轨迹完整、4/4 容器清理完成、秘密模式命中 0、负控制记忆加载 0，初始工作区和实验
  指纹两臂一致；
- 3/4 实验臂形成非空 Patch：Pylint Baseline 1,869 字节、Xarray Memory 880 字节、Xarray
  Baseline 734 字节；Pylint Memory 仍为空 Patch；
- Pylint Baseline 与 Xarray Memory 的实现补丁触发隐藏回归验证失败；Xarray Baseline 只形成
  文档修改并在实现完成前结束；Pylint Memory 连续两次请求当前阶段未暴露工具后有界终止；
- Baseline 与 Memory 成功率均为 0%，配对提升为 0。Memory 相对 Baseline 的中位 Provider
  请求差为 -2、Tool Call 差为 -3、Token 差为 -159,851、时延差为 -404,415 ms；两臂均未
  成功，这些效率差值仅用于诊断，不能表示 Memory 效果；
- ScoreCard 因只有 2 个完整配对而保持 `valid=false/incomplete_pairs`，
  `resume_claim_allowed=false`；美元成本未观测。

本轮证明未暴露工具的有界纠正是必要且有效的运行时治理：相较 v6 的 0/4 非空 Patch，v7
提升到 3/4，并首次得到真实 Validator 能力结果。当前瓶颈转移到编辑阶段过早结束：任何一次
成功编辑都会立即切换到 Diff/验证阶段，即使该编辑只完成文档或局部实现；同时 Native AUTO
策略拒绝 `apply_patch`、允许 `edit`，增加了模型在最终修改阶段的协议摩擦。下一步应修复编辑
阶段完成判定与修改工具权限一致性，再只重跑这两个配对。4-pair 扩展与 Formal 继续暂停。

不可变结果制品为 `targeted-pilot-v7-result.json`，SHA-256 为
`de59379dbd44eb292a42a9b064f7215e0cd9b0515f26734c2e0a3a213986d618`；原始 Manifest SHA-256
为 `151a31b05f14c77e951171595e608bba3513d54f59c7437dad78be4debba126a`。

### v8 编辑完成边界与 Native AUTO 权限修复

阶段机现在把“出现第一个 diff”和“补丁完成”分开处理。初始 Patch 形成后进入有界的
`patch_completion` 阶段，同时暴露 `edit`、`write`、`apply_patch` 和 `git_diff`：模型可以继续
完成同一最小修复，也可以用 `git_diff` 主动结束编辑。该窗口最晚在剩余三个 Provider 请求前
关闭，为强制 Diff、Validator 和无工具收尾分别保留一次调用，不扩大原有总预算。

Native 专用 AUTO 策略现在允许 `apply_patch` 修改一次性工作区内的普通项目路径，与 `edit`
保持一致。策略在授权前重新解析 Patch 的全部新增、修改、删除和移动目标；绝对路径、路径遍历、
项目外目标和 `.git` 等敏感路径继续拒绝。普通本地 Rook、远程渠道、EvalOps 与 Candidate 沙箱
的权限行为保持不变。阶段策略指纹升级为
`f41f299ecad15264f891b4868759877917d4e3ba447cc3687c1eb1b84f5fcc78`，对应固定输入
`native-phase-budget-v4:bounded-patch-completion-and-native-auto-apply-patch`。

两个真实故障回归测试先稳定复现失败，再完成修复：多次相关编辑可在显式 Diff 前连续落地；
Native AUTO 的项目内 `apply_patch` 自动允许，越界和敏感目标仍拒绝。验证结果：权限与阶段专项
`44 passed`；Agent Loop、Native 和 Memory Runtime 组合 `113 passed`；Benchmark CLI、readiness、
Memory ScoreCard 与权限组合 `52 passed`；Ruff 规则检查和 Native Runtime mypy 通过。
完整私有目录与密封 Validator 复核通过，Catalog 指纹仍为
`33a5692e5a50b900eeb4de5f2856b52ac628d7f82bfa013618f1a2d099a2e225`，Validator 指纹仍为
`3382235b9db99bd21abbb96e4f55a81c2719467488422d5b836933180634cafc`。

本轮没有调用真实 Provider。下一次仍应只从全新制品根重跑 Pylint/Xarray 2-pair；获得至少一个
Validator 成功实验臂后再评估 4-pair 扩展，Formal 继续暂停。

### v8 定向 2-pair 完整结果

- Experiment ID：`memory-pilot-20260802T122723408957Z-475fd116`；制品根为全新的
  `D:\RMP9_1`，运行前网络门禁通过 3/3；
- 两个任务、四个实验臂全部形成能力终态，基础设施错误 0；共使用 32/96 次 Provider 请求、
  633,036 Token、36 次 Tool Call/执行，累计实验臂时延 1,494,929 ms；
- 4/4 轨迹完整、4/4 容器清理完成、秘密模式命中 0、负控制记忆加载 0，两个配对的初始
  工作区与实验指纹一致；
- Pylint Baseline、Pylint Memory 和 Xarray Baseline 均在阶段切换后连续两次请求未暴露的
  `shell`，有界纠正策略终止 Turn，三个实验臂均为 `agent_patch_empty`；
- Xarray Memory 完成两次相关编辑和 `git_diff`，形成 2,589 字节代码与测试补丁并进入固定镜像
  Validator；回归验证以 `execution_nonzero_exit` 结束；
- Baseline 与 Memory 成功率均为 0%，配对提升为 0。Memory 相对 Baseline 的中位 Provider
  请求、Tool Call 和实际执行增量均为 2，中位 Token 增量为 108,646，中位时延减少
  35,336.5 ms；所有实验臂均失败，这些效率差值只用于诊断；
- ScoreCard 保持 `valid=false/incomplete_pairs`、`resume_claim_allowed=false`，美元成本未观测。

对 Xarray Memory 的相同固定镜像与已打补丁工作区执行零模型诊断，三个失败测试为：

- `TestAutoCombineOldAPI::test_auto_combine`：原有“变量缺失时抛错”契约被无条件放宽；
- `test_concat_compat`：原有“部分 Dataset 缺少坐标时抛错”契约被无条件放宽；
- Agent 新增的 `test_concat_missing_variables`：实际结果使用 object dtype 和 `<NA>`，且没有
  `x` 坐标；测试预期 float NaN 与显式 `x` 坐标。

补丁把问题语义实现成“对所有缺失变量使用标量填充值并沿 concat 维拼接”。密封测试要求的
语义是：对不依赖 concat 维、只存在于部分 Dataset 的变量或坐标执行合并，同时保留既有错误
边界。编辑完成窗口与 Native AUTO 权限修复已让一个实验臂形成完整补丁并抵达 Validator；
DeepSeek 对阶段工具集合的遵循和补丁语义准确性仍是当前瓶颈。

本轮 Validator 成功实验臂为 0，未满足扩展条件，因此 4-pair 没有启动，Formal 继续暂停。
不可变结果制品为 `targeted-pilot-v8-result.json`；原始 Manifest SHA-256 为
`f26c4e8c28fc0eba0cec95f7ed4a52a9b4911509ad22a275d8332ae38017c513`。

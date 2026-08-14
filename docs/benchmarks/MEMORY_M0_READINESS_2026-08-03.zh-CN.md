# Memory Benchmark v1：M0 离线就绪证据

更新时间：2026-08-03

## 结论

M0 已达到进入单独授权 M1 的离线门槛。本轮没有调用 Provider，也没有产生模型费用。

## 已验证行为

- 阶段预算纠正会明确列出 `apply_patch、edit、write`，模型获得恰好一次 edit-only 修正机会。
- Native AUTO 对 `apply_patch` 与 `edit` 使用一致的项目内修改权限。
- Memory `validation.json` 保存 regression/hidden Validator 的状态、退出码、耗时、reason code 及脱敏输出。
- Validator stdout/stderr 中的凭据形态文本和 Windows/POSIX 绝对路径会被脱敏。
- Memory v1 严格目录包含 20 个任务、10 条冻结记忆和 3 条负控制；私有 Validator Manifest 与公开 commitment 一致。
- Pilot 支持固定的 2-pair 与 4-pair 任务选择，并将扩展门槛写入终态 Manifest；门槛失败时 CLI 返回非零状态。
- 定向 Pilot 只要求提供本次选中任务的本地仓库源，不再绑定其余未执行仓库。
- Formal ScoreCard 同时记录 Baseline/Memory 的中位数、Q1、Q3 和观测数。
- Formal 报告记录非空 Patch 数、Validator 状态与 reason code 分布、基础设施重试、轨迹/制品完整度和容器清理结果。
- `rook benchmark memory report` 原子生成稳定 JSON、Markdown 和 SVG 对比图，同时冻结源 Manifest SHA-256 与环境指纹摘要；旧版缺少证据完整性字段的配对按无效证据处理。

## 离线验证结果

| 检查 | 结果 |
|---|---:|
| Benchmark/Native/Memory/Recovery/Agent Loop/权限专项 | 191 passed |
| Ruff | passed |
| mypy（Native/Memory Runtime） | passed |
| `git diff --check` | passed，只有既有换行符提示 |
| Provider 调用 | 0 |

机器可读证据位于 `benchmark/memory/v1/m0-readiness.json`。该文件记录代码、目录、选择集、Validator commitment、私有 Validator Manifest 和 Phase Policy 指纹。

## M1 固定边界

- 任务：`pylint-dev__pylint-7114`、`pydata__xarray-3364`。
- 实验臂：每个任务各一组 Baseline/Memory，共 4 个运行。
- Provider：DeepSeek；模型：`deepseek-v4-flash`；禁止回退。
- 工作目录：全新目录，禁止续跑历史实验。
- 请求上限：包含一次干净配对重试时，实际 Provider 请求总上限 96。
- 停止条件：readiness 失败、重复基础设施失败或负控制记忆被加载。
- 扩展门槛：至少一个实验臂通过隐藏 Validator，四臂终态和制品完整，秘密泄漏与负控制加载均为 0。

M1 结果只用于决定是否进入 4-pair Pilot，不作为 Formal 或简历效果指标。

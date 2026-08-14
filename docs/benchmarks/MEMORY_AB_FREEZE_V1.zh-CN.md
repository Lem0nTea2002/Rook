# Memory A/B v1 冻结记录

## 冻结结论

Memory A/B v1 已于 2026-08-01 完成离线冻结。10 条用户确认的不可变项目记忆分别对应
2 个未见历史任务，共 20 个配对任务，覆盖 Django、Matplotlib、Astropy、Xarray、Pylint
和 SymPy 六个仓库。Baseline 和 Memory 两臂将在相同 base commit、模型、工具 Schema、
容器和请求预算下执行，顺序由 task hash 稳定交替。

| Seed | Memory task A | Memory task B |
|---|---|---|
| 01 相邻测试与回归 | `django__django-13220` | `matplotlib__matplotlib-22835` |
| 02 路径恢复 | `django__django-12125` | `pylint-dev__pylint-7114` |
| 03 项目入口 | `django__django-13660` | `pydata__xarray-3364` |
| 04 语义不变量 | `astropy__astropy-12907` | `django__django-11019` |
| 05 随机与确定性 | `django__django-11583` | `matplotlib__matplotlib-23299` |
| 06 版本兼容 | `astropy__astropy-14995` | `django__django-15902` |
| 07 配置负路径 | `django__django-11620` | `django__django-13448` |
| 08 状态清理 | `django__django-12700` | `django__django-16379` |
| 09 可执行文档 | `astropy__astropy-14182` | `pydata__xarray-5131` |
| 10 多输出后端 | `matplotlib__matplotlib-23964` | `sympy__sympy-16106` |

## 证据指纹

- SWE-bench Lite revision：`6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`；
- 数据集快照 SHA-256：`311c90a7a28038402ff17fcfc09d7b866d5c60cfa0909d70163fb0f4b40fb299`；
- Selection SHA-256：`001c1fce51ff052e7223bafc79e0bc8e52fe1e36dd405084c0ca195160654167`；
- Catalog fingerprint：`33a5692e5a50b900eeb4de5f2856b52ac628d7f82bfa013618f1a2d099a2e225`；
- Validator manifest SHA-256：`3382235b9db99bd21abbb96e4f55a81c2719467488422d5b836933180634cafc`；
- Tool Schema fingerprint：`1764bd818dab06a14336505173a12050`。

离线 `rook benchmark memory verify` 已验证 10 条记忆、20 个任务、3 个负控制和全部私有
Validator。公开目录不包含 gold patch、隐藏测试、Validator 命令或期望输出。

## 证据边界

任务内容来自公开历史 SWE-bench Lite，基础模型可能在训练阶段接触过相关公开内容。这里的
“未见”表示 Rook 本轮在记忆冻结前没有访问这些任务、原补丁和隐藏 Validator。任务选择在
10 条 memory content hash 固定后完成，Pilot 运行前禁止调整任务映射或记忆正文。

当前冻结证明实验输入完整且隔离边界成立。4-pair Pilot 固定从前四条记忆中各取一个任务，
并优先选择尚未覆盖的仓库；当前样本覆盖 Django、Pylint、Xarray 和 Astropy。

历史 4-pair Pilot 经运行时修复后得到 Baseline 25%、Memory 25%，只证明执行链路能够
产生能力结果。最新 v8 定向 2-pair 在 Pylint/Xarray 上得到 Baseline 与 Memory 成功率均为
0%；Xarray Memory 形成非空 Patch，但破坏既有行为，Validator 成功臂为 0。完整失败演进见
[Memory A/B v1 Pilot 报告](MEMORY_PILOT_V1_2026-08-01.zh-CN.md)。M1 必须从全新目录重新
执行这两个配对；达到至少一个 Validator 成功臂前，4-pair 扩展关闭，20-pair Formal 保持暂停。

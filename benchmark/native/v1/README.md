# Rook Native Task Set v1

该目录只保存 Agent 可见的任务目录、公开来源和密封 Validator commitment。
隐藏测试补丁、Validator 命令及镜像细节必须保存在独立私有目录，不能提交到这里，
也不能进入 Agent Prompt、Session、Tool Result 或工作区。

当前状态：已冻结，尚未执行第一次 live 模型调用。

冻结制品：

1. `tasks.jsonl`：Agent 可见的 30 个任务。
2. `PROVENANCE.json`：固定数据集 revision、目录指纹和 Agent 数据边界。
3. `validator-commitment.json`：私有 Validator 清单的公开承诺。

执行要求：

1. 第一次模型调用前运行 `rook benchmark native verify`。
2. Formal 与必要救援全部结束后运行 `rook benchmark native reveal <experiment-id>`；
   该命令会原子写入 `validator-reveal.json`，此后 v1 不能再次作为 sealed holdout。

冻结器不会自动猜测类别，不会写入 gold patch，也不会发起 GitHub 写操作。

# Rook Forge Offline Demo / 离线演示

`rook eval demo` is the shortest reproducible proof of the Rook Forge control plane. It runs the complete lifecycle with deterministic Fake Agents, so it needs no provider configuration, network access, Codex login, API key, or model budget.

`rook eval demo` 是 Rook Forge 控制面的最短可复现实验。它只使用确定性 Fake Agent 跑完整生命周期，不需要 Provider 配置、网络、Codex 登录、API Key 或模型额度。

## Run / 运行

From any project directory after installing Rook:

安装 Rook 后，在任意项目目录执行：

```powershell
rook eval demo
```

Choose another parent directory when needed:

如需指定输出父目录：

```powershell
rook eval demo --output .rook\my-forge-demo
```

Each invocation creates a new `run-<id>` directory. It never writes a Skill into the real project's `.agents/skills`; the simulated repository deployment lives inside that run directory.

每次执行都会创建新的 `run-<id>` 目录。命令不会向真实项目的 `.agents/skills` 写入 Skill；模拟的仓库级部署只存在于本次 run 目录内。

## Lifecycle checkpoints / 生命周期检查点

1. Store v1 and v2 as immutable Candidate versions.
2. Run Direct, Transfer, Regression, and Adversarial paired exams for Fake Rook and Fake Codex targets.
3. Produce ScoreCards and `promoted` automatic gate decisions.
4. Verify that gate decisions remain inactive before explicit human approval.
5. Approve and deploy v1 independently to both targets.
6. Approve and replace it with v2.
7. Modify the Rook-managed Codex `SKILL.md`, verify it becomes `drifted`, then restore the exact managed bytes and verify it becomes `active`.
8. Roll both targets back through the transactional release path to the previously approved v1.
9. Verify Registry pointers, Rook discovery, Codex `SKILL.md`, reports, and content hashes agree.

对应中文：保存两个不可变 Candidate 版本；对 Fake Rook/Fake Codex 执行四类隔离配对考试；生成 ScoreCard 和门禁结果；证明门禁通过不会自动上线；分别审批并部署 v1、v2；手工修改受管 Codex Skill 并验证 `drifted`，恢复原始字节后重新变为 `active`；最后通过事务发布路径把两个目标回滚到已审批的 v1，并核对 Registry、运行时发现、Codex 文件、报告与哈希一致。

## Artifacts / 产物

```text
.rook/forge-demo/run-<id>/
├── demo-summary.json
├── demo-summary.md
├── .rook/
│   ├── evalops/artifacts/
│   └── skill-registry/
└── .agents/skills/rook-forge-demo-skill/
```

`demo-summary.json` is machine-readable evidence; `demo-summary.md` is the concise human handoff. Both explicitly record `external_calls: false` and `model_costs: false`.

`demo-summary.json` 是机器可读证据，`demo-summary.md` 是面向人工复核的摘要；二者都明确记录没有外部调用和模型费用。

This demo proves orchestration, policy, approval, deployment, drift-safe ownership, and rollback behavior. It does **not** prove that a real model improves on real tasks. Only separately authorized live Calibration/Pilot/Formal reports can support model-effect claims.

本演示证明编排、门禁、审批、部署、所有权保护和回滚链路正确，但**不能**证明真实模型在真实任务上获得提升。模型效果只能由另行授权的 Calibration/Pilot/Formal 报告支持。

A redacted record from an actual local execution is checked in as
[`forge-lifecycle-2026-07-24.json`](evidence/forge-lifecycle-2026-07-24.json).
It contains real immutable approval/release IDs and artifact hashes while
retaining the explicit Fake-Agent claim boundary.

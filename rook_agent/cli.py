"""Command-line entry point for single-turn Rook runs."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from rook_agent.agent.loop_limits import AgentLoopLimits
from rook_agent.app.factory import create_rook_app
from rook_agent.config import load_config
from rook_agent.config.credentials import read_api_key
from rook_agent.config.onboarding import run_setup_wizard
from rook_agent.config.settings import default_global_config_path, project_config_path, render_default_config
from rook_agent.eval.adapter import RookCodingAgentAdapter
from rook_agent.eval.tasks import CodingTask
from rook_agent.providers.factory import ProviderConfigError
from rook_agent.providers.presets import PROVIDER_PRESETS


@dataclass(frozen=True, slots=True)
class CliConfig:
    project_root: Path
    data_root: Path | None
    session_id: str | None
    provider_name: str | None
    message: str
    max_tool_rounds: int | None = None
    benchmark: bool = False


CliRunner = Callable[[CliConfig], str]


class ChatRunnerLike(Protocol):
    last_pending_input: object | None

    def run_user_turn(self, content: str):
        ...

    def resume_with_user_input(self, request_id: str, answer: str):
        ...


def read_message(message: str | None, *, stdin_text: str | None = None) -> str:
    """Return a user message from an argument or stdin."""

    if message is not None:
        return message.strip()
    text = sys.stdin.read() if stdin_text is None else stdin_text
    return text.strip()


def _nonempty_text(value: str) -> str:
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("value must not be empty")
    return text


def _add_native_catalog_arguments(
    parser: argparse.ArgumentParser,
    *,
    validators_required: bool,
) -> None:
    parser.add_argument(
        "--tasks",
        default="benchmark/native/v1/tasks.jsonl",
    )
    parser.add_argument(
        "--validators",
        required=validators_required,
        default=None,
        help="私有密封 Validator 清单；绝不会传给 Agent。",
    )
    parser.add_argument(
        "--commitment",
        default="benchmark/native/v1/validator-commitment.json",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="格式为 REPOSITORY=LOCAL_MIRROR；每个仓库重复一次。",
    )
    parser.add_argument(
        "--root",
        default=".rook/benchmarks/native-v1",
    )


def _add_benchmark_live_authorization(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="授权本次命令调用已配置的模型 Provider。",
    )
    parser.add_argument(
        "--allow-costs",
        action="store_true",
        help="确认本次命令会消耗模型额度或产生费用。",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Rook Coding Agent or its Rook Forge Skill governance commands."
    )
    subparsers = parser.add_subparsers(dest="command")
    config_parser = subparsers.add_parser("config", help="Inspect or initialize Rook configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("path", help="Show global and project config paths.")
    config_subparsers.add_parser("show", help="Show effective provider configuration without secrets.")
    setup_parser = config_subparsers.add_parser(
        "setup",
        help="Interactively configure a model Provider and store its API key securely.",
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the global Provider configuration instead of reusing it.",
    )
    init_parser = config_subparsers.add_parser("init", help="Create a starter global config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite the existing global config.")

    channel_parser = subparsers.add_parser(
        "channel",
        help="Pair and serve private Feishu or WeChat conversations.",
    )
    channel_subparsers = channel_parser.add_subparsers(
        dest="channel_command",
        required=True,
    )
    channel_setup = channel_subparsers.add_parser(
        "setup",
        help="Create or configure an official channel application securely.",
    )
    channel_setup.add_argument("setup_channel", choices=("feishu",))
    channel_setup.add_argument(
        "--app-id",
        default=None,
        help="Configure an existing Feishu app; omit to create a dedicated app by QR scan.",
    )
    channel_login = channel_subparsers.add_parser(
        "login",
        help="Log in to an official user-authorized channel.",
    )
    channel_login.add_argument("login_channel", choices=("weixin",))
    channel_project = channel_subparsers.add_parser(
        "project",
        help="Manage the absolute local project whitelist.",
    )
    channel_project_subparsers = channel_project.add_subparsers(
        dest="project_command",
        required=True,
    )
    channel_project_add = channel_project_subparsers.add_parser("add")
    channel_project_add.add_argument("alias", type=_nonempty_text)
    channel_project_add.add_argument("--path", required=True)
    channel_pair = channel_subparsers.add_parser(
        "pair",
        help="Create a single-use private-chat pairing code.",
    )
    channel_pair_subparsers = channel_pair.add_subparsers(
        dest="pair_command",
        required=True,
    )
    channel_pair_create = channel_pair_subparsers.add_parser("create")
    channel_pair_create.add_argument(
        "--channel",
        required=True,
        choices=("feishu", "weixin"),
    )
    channel_pair_create.add_argument("--project", required=True, type=_nonempty_text)
    channel_serve = channel_subparsers.add_parser(
        "serve",
        help="Run the foreground local channel gateway.",
    )
    channel_serve.add_argument(
        "--channels",
        required=True,
        help="Comma-separated channels: feishu,weixin.",
    )
    channel_smoke = channel_subparsers.add_parser(
        "smoke",
        help="Run real channels with a local Fake Runner and no model calls.",
    )
    channel_smoke.add_argument(
        "--channels",
        required=True,
        help="Comma-separated channels: feishu,weixin.",
    )
    channel_status = channel_subparsers.add_parser(
        "status",
        help="Show non-secret channel configuration and health.",
    )
    channel_status.add_argument("--json", action="store_true")
    channel_autostart = channel_subparsers.add_parser(
        "autostart",
        help="Manage the current-user Windows startup task.",
    )
    channel_autostart_subparsers = channel_autostart.add_subparsers(
        dest="autostart_command",
        required=True,
    )
    channel_autostart_install = channel_autostart_subparsers.add_parser("install")
    channel_autostart_install.add_argument(
        "--channels",
        default="feishu,weixin",
    )
    channel_autostart_subparsers.add_parser("remove")
    channel_autostart_subparsers.add_parser("status")

    review_parser = subparsers.add_parser(
        "review",
        help="Submit read-only workspace reviews to EvoAgent.",
    )
    review_subparsers = review_parser.add_subparsers(dest="review_command", required=True)
    review_subparsers.add_parser("doctor", help="Check EvoAgent, authentication and reviewers.")
    review_login = review_subparsers.add_parser(
        "login", help="Store the EvoAgent administrator password in the system keyring."
    )
    review_login.add_argument("--username", default="admin")
    review_run = review_subparsers.add_parser("run", help="Submit a read-only review.")
    review_run.add_argument("--target", choices=("workspace", "range", "commit"), required=True)
    review_run.add_argument("--from", dest="from_ref", default=None)
    review_run.add_argument("--to", dest="to_ref", default=None)
    review_run.add_argument("--commit", default=None)
    review_run.add_argument("--reviewers", default=None, help="Comma-separated local,ocr reviewers.")
    review_report = review_subparsers.add_parser("report", help="Read one EvoAgent task report.")
    review_report.add_argument("task_id")

    eval_parser = subparsers.add_parser("eval", help="Run and inspect Rook Forge Skill exams.")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", required=True)
    doctor_parser = eval_subparsers.add_parser("doctor", help="Probe Rook Forge Agent capabilities.")
    doctor_parser.add_argument("--agents", default="rook,codex", help="Comma-separated Agents to probe.")
    demo_parser = eval_subparsers.add_parser(
        "demo", help="Run the complete offline Rook Forge lifecycle with Fake Agents."
    )
    demo_parser.add_argument(
        "--output",
        default=".rook/forge-demo",
        help="Parent directory for the isolated demo run (default: .rook/forge-demo).",
    )
    pr_gate_parser = eval_subparsers.add_parser(
        "pr-gate",
        help="Validate changed Rook Forge assets without external or model calls.",
    )
    pr_gate_parser.add_argument(
        "--base",
        required=True,
        help="Base Git ref or commit for the pull-request diff.",
    )
    pr_gate_parser.add_argument(
        "--head",
        required=True,
        help="Head Git ref or commit for the pull-request diff.",
    )
    pr_gate_parser.add_argument(
        "--output",
        default=".rook/pr-gate/report.json",
        help="Atomic JSON report path inside the project.",
    )
    eval_run_parser = eval_subparsers.add_parser("run", help="Run a Rook Forge exam for one stored Skill Candidate.")
    eval_run_parser.add_argument("--skill-path", required=True, help="Candidate version directory.")
    eval_run_parser.add_argument("--suite", required=True, help="Eval suite TOML manifest.")
    eval_run_parser.add_argument("--agents", required=True, help="Comma-separated Agents to evaluate.")
    eval_run_parser.add_argument(
        "--model",
        type=_nonempty_text,
        default=None,
        help="Explicit Codex model recorded in the target fingerprint.",
    )
    eval_run_parser.add_argument(
        "--inherit-proxy",
        action="store_true",
        help="Explicitly pass configured proxy environment variables to Codex.",
    )
    eval_run_parser.add_argument("--repetitions", type=_positive_int, default=1)
    eval_run_parser.add_argument(
        "--families",
        default="content,routing",
        help="Comma-separated treatment families: content,routing.",
    )
    eval_run_parser.add_argument(
        "--phase",
        choices=("auto", "fast", "full"),
        default="auto",
        help="Run Fast then Full automatically, or only one phase.",
    )
    eval_run_parser.add_argument(
        "--fast-count-per-category",
        type=_positive_int,
        default=1,
    )
    eval_run_parser.add_argument(
        "--measurement-only",
        action="store_true",
        help="Write evidence and reports without mutating the promotion registry.",
    )
    eval_run_parser.add_argument(
        "--stop-on-infrastructure-exclusion",
        action="store_true",
        help="Stop before the next Agent call after the first infrastructure exclusion.",
    )
    eval_run_parser.add_argument("--allow-external", action="store_true", help="Allow external Agent/model calls.")
    eval_run_parser.add_argument("--allow-costs", action="store_true", help="Acknowledge possible model costs.")
    report_parser = eval_subparsers.add_parser("report", help="Read an immutable Rook Forge report.")
    report_parser.add_argument("experiment_id")
    record_parser = eval_subparsers.add_parser(
        "record-decision",
        help="Verify and record a measurement-only decision without rerunning the Agent.",
    )
    record_parser.add_argument("evaluation_id")
    record_parser.add_argument("--agent", required=True, choices=("rook", "codex"))
    record_parser.add_argument("--skill-path", required=True, help="Candidate version directory.")
    record_parser.add_argument("--suite", required=True, help="Current Eval suite TOML manifest.")
    record_parser.add_argument(
        "--scorecard-sha256",
        required=True,
        help="Expected SHA-256 of the immutable scorecard.json evidence.",
    )
    trends_parser = eval_subparsers.add_parser(
        "trends", help="Compare immutable ScoreCards and summarize EvalOps SLOs."
    )
    trends_parser.add_argument("name", help="Skill name to inspect.")
    trends_parser.add_argument("--agent", choices=("rook", "codex"), default=None)
    trends_parser.add_argument("--limit", type=_positive_int, default=20)
    trends_parser.add_argument("--json", action="store_true", help="Print stable JSON instead of Markdown.")

    skill_parser = subparsers.add_parser("skill", help="Inspect or change Rook Forge Skill state.")
    skill_subparsers = skill_parser.add_subparsers(dest="skill_command", required=True)
    status_parser = skill_subparsers.add_parser("status", help="Show candidate and promotion state.")
    status_parser.add_argument("name")
    approve_parser = skill_subparsers.add_parser(
        "approve", help="Approve and deploy one promoted, non-stale Skill decision."
    )
    approve_parser.add_argument("name")
    approve_parser.add_argument("--agent", required=True, choices=("rook", "codex"))
    approve_parser.add_argument("--decision-id", required=True)
    approve_parser.add_argument("--suite", required=True, help="Current Eval suite TOML manifest.")
    approve_parser.add_argument("--approver", required=True, type=_nonempty_text)
    approve_parser.add_argument("--reason", required=True, type=_nonempty_text)
    history_parser = skill_subparsers.add_parser(
        "history", help="Show immutable gate, approval, and release history."
    )
    history_parser.add_argument("name")
    stage_parser = skill_subparsers.add_parser(
        "stage", help="Stage a strict TOML Skill bundle as an inactive quarantined candidate."
    )
    stage_parser.add_argument("--bundle", required=True, help="Manual Skill bundle TOML file.")
    rollback_parser = skill_subparsers.add_parser("rollback", help="Roll back an active Skill version.")
    rollback_parser.add_argument("name")
    rollback_parser.add_argument("--agent", required=True, choices=("rook", "codex"))
    rollback_parser.add_argument("--to-version", type=_positive_int, required=True)
    rollback_parser.add_argument("--approver", required=True, type=_nonempty_text)
    rollback_parser.add_argument("--reason", required=True, type=_nonempty_text)
    export_parser = skill_subparsers.add_parser("export", help="Export an evaluated Skill to a staging directory.")
    export_parser.add_argument("name")
    export_parser.add_argument("--agent", required=True, choices=("rook", "codex"))
    export_parser.add_argument("--output", required=True)

    scale_parser = subparsers.add_parser(
        "scale",
        help="Run and inspect the durable Rook execution platform.",
    )
    scale_subparsers = scale_parser.add_subparsers(
        dest="scale_command",
        required=True,
    )
    scale_benchmark = scale_subparsers.add_parser(
        "benchmark",
        help="Run a deterministic, cost-free concurrency and recovery benchmark.",
    )
    scale_benchmark.add_argument("--jobs", type=_positive_int, default=500)
    scale_benchmark.add_argument(
        "--workers",
        default="10,25,50",
        help="Comma-separated worker counts, each in the range 1-50.",
    )
    scale_benchmark.add_argument(
        "--work-milliseconds",
        type=float,
        default=5,
        help="Deterministic simulated work per job.",
    )
    scale_benchmark.add_argument(
        "--fault-every",
        type=int,
        default=17,
        help="Inject one recoverable failure every N jobs; 0 disables faults.",
    )
    scale_benchmark.add_argument(
        "--output",
        default=".rook/scale/benchmark.json",
    )
    scale_benchmark.add_argument(
        "--markdown",
        default=None,
        help="Optional Markdown report path.",
    )
    scale_enqueue = scale_subparsers.add_parser(
        "enqueue",
        help="Idempotently enqueue one strict Docker job JSON document.",
    )
    scale_enqueue.add_argument("--db", required=True)
    scale_enqueue.add_argument("--spec", required=True)
    scale_enqueue.add_argument("--idempotency-key", required=True)
    scale_enqueue.add_argument("--max-attempts", type=_positive_int, default=3)
    scale_worker = scale_subparsers.add_parser(
        "worker",
        help="Drain Docker jobs with bounded concurrency and expiring leases.",
    )
    scale_worker.add_argument("--db", required=True)
    scale_worker.add_argument("--workspace-root", required=True)
    scale_worker.add_argument(
        "--allow-image",
        action="append",
        required=True,
        help="Allowed image@sha256 digest; repeat for multiple images.",
    )
    scale_worker.add_argument("--workers", type=_positive_int, default=10)
    scale_worker.add_argument("--lease-seconds", type=float, default=120)
    scale_worker.add_argument("--max-timeout-seconds", type=float, default=1800)
    scale_worker.add_argument("--prometheus-port", type=int, default=None)
    scale_worker.add_argument("--otel-endpoint", default=None)

    repository_parser = subparsers.add_parser(
        "repo",
        help="Inspect and materialize immutable full-repository tasks.",
    )
    repository_subparsers = repository_parser.add_subparsers(
        dest="repo_command",
        required=True,
    )
    verify_catalog = repository_subparsers.add_parser(
        "verify-catalog",
        help="Strictly validate a full-repository JSONL task catalog.",
    )
    verify_catalog.add_argument("--tasks", required=True)
    export_swebench = repository_subparsers.add_parser(
        "export-swebench",
        help="Export agent-visible fields for the existing SWE-bench runner.",
    )
    export_swebench.add_argument("--tasks", required=True)
    export_swebench.add_argument("--output", required=True)
    materialize_repository = repository_subparsers.add_parser(
        "materialize",
        help="Clone and detach one catalog task at its exact base commit.",
    )
    materialize_repository.add_argument("--tasks", required=True)
    materialize_repository.add_argument("--task-id", required=True)
    materialize_repository.add_argument("--workdir", required=True)
    materialize_repository.add_argument(
        "--source",
        default=None,
        help="Optional trusted local repository or mirror.",
    )
    materialize_repository.add_argument(
        "--allow-network",
        action="store_true",
        help="Explicitly allow a GitHub clone when no local source is supplied.",
    )
    issue_pr_demo = repository_subparsers.add_parser(
        "issue-pr-demo",
        help="Build a zero-model local Issue-to-reviewed-Draft-PR evidence bundle.",
    )
    issue_pr_demo.add_argument(
        "--output",
        default=".rook/issue-pr-demo",
        help="New output directory; existing paths are never overwritten.",
    )
    issue_pr_demo.add_argument(
        "--approver",
        required=True,
        type=_nonempty_text,
        help="Human reviewer identity recorded in the immutable demo ledger.",
    )
    contribution_record = repository_subparsers.add_parser(
        "contribution-record",
        help="Append one hash-chained upstream contribution state event.",
    )
    contribution_record.add_argument("--ledger", required=True)
    contribution_record.add_argument("--task-id", required=True)
    contribution_record.add_argument("--repository", required=True)
    contribution_record.add_argument("--issue-url", required=True)
    contribution_record.add_argument(
        "--status",
        required=True,
        choices=(
            "screened",
            "awaiting_human_claim",
            "claimed",
            "in_progress",
            "ready_for_human_review",
            "reviewed",
            "submitted",
            "accepted",
            "rejected",
            "withdrawn",
            "superseded",
            "blocked",
        ),
    )
    contribution_record.add_argument("--actor", required=True, type=_nonempty_text)
    contribution_record.add_argument(
        "--reason-code",
        required=True,
        type=_nonempty_text,
    )
    contribution_record.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="GitHub HTTPS URL or artifact:relative/path; repeat as needed.",
    )
    contribution_record.add_argument(
        "--detail",
        action="append",
        default=[],
        help="Non-sensitive key=value metadata; repeat as needed.",
    )
    contribution_record.add_argument(
        "--recorded-at",
        default=None,
        help="Optional ISO-8601 UTC timestamp for imported evidence.",
    )
    contribution_history = repository_subparsers.add_parser(
        "contribution-history",
        help="Verify and display the immutable contribution event chain.",
    )
    contribution_history.add_argument("--ledger", required=True)
    contribution_history.add_argument("--task-id", default=None)
    contribution_history.add_argument(
        "--json",
        action="store_true",
        help="Print stable JSON instead of the compact event list.",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="验证并运行 Native、Recovery 与 Memory 证据套件。",
    )
    benchmark_families = benchmark_parser.add_subparsers(
        dest="benchmark_family",
        required=True,
    )

    native_parser = benchmark_families.add_parser(
        "native",
        help="运行密封的完整仓库 Native Task Set。",
    )
    native_commands = native_parser.add_subparsers(
        dest="native_command",
        required=True,
    )
    native_verify = native_commands.add_parser("verify")
    _add_native_catalog_arguments(native_verify, validators_required=False)
    native_smoke = native_commands.add_parser("smoke")
    _add_native_catalog_arguments(native_smoke, validators_required=True)
    _add_benchmark_live_authorization(native_smoke)
    native_run = native_commands.add_parser("run")
    native_run.add_argument("--phase", choices=("pilot", "formal"), required=True)
    _add_native_catalog_arguments(native_run, validators_required=True)
    _add_benchmark_live_authorization(native_run)
    native_rescue = native_commands.add_parser("rescue")
    native_rescue.add_argument("experiment_id")
    _add_native_catalog_arguments(native_rescue, validators_required=True)
    native_rescue.add_argument(
        "--hint",
        action="append",
        required=True,
        help="格式为 TASK_ID=TEXT；第二条有界提示可重复传入一次。",
    )
    _add_benchmark_live_authorization(native_rescue)
    native_report = native_commands.add_parser("report")
    native_report.add_argument("experiment_id")
    native_report.add_argument(
        "--root",
        default=".rook/benchmarks/native-v1",
    )
    native_reveal = native_commands.add_parser(
        "reveal",
        help="在完整 Formal 后一次性公开 Validator，并结束 v1 sealed 状态。",
    )
    _add_native_catalog_arguments(native_reveal, validators_required=True)
    native_reveal.add_argument("experiment_id")
    native_reveal.add_argument(
        "--output",
        default="benchmark/native/v1/validator-reveal.json",
    )

    recovery_parser = benchmark_families.add_parser(
        "recovery",
        help="在冻结轨迹 holdout 上评测 RecoveryDetector。",
    )
    recovery_commands = recovery_parser.add_subparsers(
        dest="recovery_command",
        required=True,
    )
    recovery_verify = recovery_commands.add_parser("verify")
    recovery_verify.add_argument(
        "--catalog",
        default="benchmark/recovery/v1/traces.jsonl",
    )
    recovery_score = recovery_commands.add_parser("score")
    recovery_score.add_argument(
        "--catalog",
        default="benchmark/recovery/v1/traces.jsonl",
    )
    recovery_score.add_argument(
        "--output",
        default=".rook/benchmarks/recovery-v1/score.json",
    )

    memory_parser = benchmark_families.add_parser(
        "memory",
        help="运行配对的项目记忆 A/B 实验。",
    )
    memory_commands = memory_parser.add_subparsers(
        dest="memory_command",
        required=True,
    )
    memory_seed_validate = memory_commands.add_parser(
        "seed-validate",
        help="对 2-3 个已审阅 Seed 执行有界的真实恢复验证。",
    )
    memory_seed_validate.add_argument(
        "--cases",
        default="benchmark/memory/v1/recovery-seeds.json",
    )
    memory_seed_validate.add_argument(
        "--root",
        default=".rook/benchmarks/memory-seed-validation",
    )
    memory_seed_validate.add_argument(
        "--seed",
        action="append",
        default=[],
        help="只重跑指定 Seed；必须重复传入 2-3 次。",
    )
    _add_benchmark_live_authorization(memory_seed_validate)
    memory_verify = memory_commands.add_parser("verify")
    memory_verify.add_argument(
        "--catalog",
        default="benchmark/memory/v1/catalog.json",
    )
    memory_verify.add_argument(
        "--validators",
        default=None,
        help="可选的私有 Memory sealed task manifest；不会传给 Agent。",
    )
    memory_run = memory_commands.add_parser("run")
    memory_run.add_argument("--phase", choices=("pilot", "formal"), required=True)
    memory_run.add_argument(
        "--catalog",
        default="benchmark/memory/v1/catalog.json",
    )
    memory_run.add_argument("--validators", required=True)
    memory_run.add_argument("--source", action="append", default=[])
    memory_run.add_argument(
        "--task",
        action="append",
        default=[],
        help="定向 Pilot 任务；使用时必须重复传入两个或四个不同 task id。",
    )
    memory_run.add_argument(
        "--root",
        default=".rook/benchmarks/memory-v1",
    )
    _add_benchmark_live_authorization(memory_run)
    memory_report = memory_commands.add_parser("report")
    memory_report.add_argument("experiment_id")
    memory_report.add_argument(
        "--root",
        default=".rook/benchmarks/memory-v1",
    )

    parser.add_argument("--project", default=".", help="Project root for tools and AGENTS.md.")
    parser.add_argument("--data-root", default=None, help="Directory for Rook session data.")
    parser.add_argument("--session-id", default=None, help="Session id to create or reuse.")
    parser.add_argument("--provider", default=None, help="Provider name override.")
    parser.add_argument("--message", default=None, help="Single user message. Reads stdin when omitted.")
    parser.add_argument("--interactive", action="store_true", help="Run a line-oriented interactive session.")
    parser.add_argument("--tui", action="store_true", help="Run the Textual TUI.")
    parser.add_argument("--auto-approve", action="store_true", help="Automatically answer permission confirmations with allow_once.")
    parser.add_argument("--max-tool-rounds", type=_positive_int, default=None, help="Override per-turn tool round limit.")
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run the message with the isolated non-interactive benchmark permission policy.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: CliRunner | None = None,
    stdin_text: str | None = None,
    evalops_runner: Callable[[argparse.Namespace], int] | None = None,
    scale_runner: Callable[[argparse.Namespace], int] | None = None,
    repository_runner: Callable[[argparse.Namespace], int] | None = None,
    channel_runner: Callable[[argparse.Namespace], int] | None = None,
    benchmark_runner: Callable[[argparse.Namespace], int] | None = None,
    review_runner: Callable[[argparse.Namespace], int] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "config":
        return run_config_command(args)
    if args.command == "channel":
        if channel_runner is None:
            from rook_agent.channels.cli import run_channel_command

            channel_runner = run_channel_command
        try:
            return channel_runner(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "review":
        if review_runner is None:
            from rook_agent.review.cli import main_review_command

            review_runner = main_review_command
        try:
            return review_runner(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command in {"eval", "skill"}:
        if evalops_runner is None:
            from rook_agent.evalops.cli import run_evalops_command

            evalops_runner = run_evalops_command
        try:
            return evalops_runner(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "scale":
        if scale_runner is None:
            from rook_agent.execution.cli import run_scale_command

            scale_runner = run_scale_command
        try:
            return scale_runner(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "repo":
        if repository_runner is None:
            from rook_agent.execution.repo_cli import run_repository_command

            repository_runner = run_repository_command
        try:
            return repository_runner(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if args.command == "benchmark":
        if benchmark_runner is None:
            from rook_agent.benchmarks.cli import run_benchmark_command

            benchmark_runner = run_benchmark_command
        try:
            return benchmark_runner(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.tui or (args.message is None and stdin_text is None and sys.stdin.isatty() and not args.interactive):
        config = CliConfig(
            project_root=Path(args.project),
            data_root=Path(args.data_root) if args.data_root is not None else None,
            session_id=args.session_id,
            provider_name=args.provider,
            message="",
            max_tool_rounds=args.max_tool_rounds,
            benchmark=args.benchmark,
        )
        try:
            app = _create_cli_app_with_onboarding(
                config,
                allow_prompt=_can_prompt_for_setup(),
            )
            app.run()
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.interactive:
        config = CliConfig(
            project_root=Path(args.project),
            data_root=Path(args.data_root) if args.data_root is not None else None,
            session_id=args.session_id,
            provider_name=args.provider,
            message="",
            max_tool_rounds=args.max_tool_rounds,
            benchmark=args.benchmark,
        )
        try:
            app = _create_cli_app_with_onboarding(
                config,
                allow_prompt=_can_prompt_for_setup(),
            )
            lines = stdin_text.splitlines() if stdin_text is not None else None
            run_repl(app.chat_runner, lines, auto_approve=args.auto_approve)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    message = read_message(args.message, stdin_text=stdin_text)
    if not message:
        print("error: message is required via --message or stdin", file=sys.stderr)
        return 2

    config = CliConfig(
        project_root=Path(args.project),
        data_root=Path(args.data_root) if args.data_root is not None else None,
        session_id=args.session_id,
        provider_name=args.provider,
        message=message,
        max_tool_rounds=args.max_tool_rounds,
        benchmark=args.benchmark,
    )
    run = runner or run_single_turn
    try:
        output = run(config)
    except ProviderConfigError as exc:
        print(f"error: {_provider_setup_guidance(exc)}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if output:
        print(output)
    return 0


def run_single_turn(config: CliConfig) -> str:
    if config.benchmark:
        return run_benchmark_turn(config)
    app = create_cli_app(config)
    response = app.chat_runner.run_user_turn(config.message)
    return response.content


def run_benchmark_turn(config: CliConfig) -> str:
    """Run one isolated benchmark task with a repo-local non-interactive policy."""

    adapter = RookCodingAgentAdapter(
        model_name_or_path="rook-benchmark",
        provider_name=config.provider_name,
        session_root=config.data_root or (config.project_root.resolve().parent / ".rook-benchmark"),
        limits=_benchmark_limits(config.max_tool_rounds),
    )
    result = adapter.run_task(
        CodingTask(
            instance_id=config.session_id or config.project_root.resolve().name,
            repo_path=config.project_root,
            problem_statement=config.message,
            metadata={"benchmark": "rook-cli"},
        )
    )
    return result.raw_response


def create_cli_app(config: CliConfig):
    provider = None
    if config.provider_name is not None:
        from rook_agent.providers.factory import create_provider

        provider = create_provider(config.provider_name, project_root=config.project_root)
    app = create_rook_app(
        project_root=config.project_root,
        data_root=config.data_root,
        provider=provider,
        session_id=config.session_id,
    )
    if config.max_tool_rounds is not None:
        app.chat_runner.limits = AgentLoopLimits.default().with_max_tool_rounds(config.max_tool_rounds)
    return app


def _create_cli_app_with_onboarding(
    config: CliConfig,
    *,
    allow_prompt: bool,
    setup_runner: Callable[..., object] = run_setup_wizard,
):
    try:
        return create_cli_app(config)
    except ProviderConfigError as exc:
        if not allow_prompt:
            raise ProviderConfigError(_provider_setup_guidance(exc)) from exc
        setup_runner(
            project_root=config.project_root,
            provider_name=config.provider_name,
            force=False,
        )
        return create_cli_app(config)


def _can_prompt_for_setup() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _provider_setup_guidance(error: Exception) -> str:
    return f"{error}. Run `rook config setup` in an interactive terminal."


def run_config_command(args: argparse.Namespace) -> int:
    command = args.config_command or "show"
    project_root = Path(args.project)
    if command == "path":
        print(f"global: {default_global_config_path()}")
        print(f"project: {project_config_path(project_root)}")
        return 0
    if command == "init":
        path = default_global_config_path()
        if path.exists() and not args.force:
            print(f"config already exists: {path}", file=sys.stderr)
            print("use --force to overwrite", file=sys.stderr)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_default_config(), encoding="utf-8")
        print(f"created: {path}")
        return 0
    if command == "setup":
        try:
            run_setup_wizard(
                project_root=project_root,
                provider_name=args.provider,
                force=bool(args.force),
            )
        except (EOFError, KeyboardInterrupt):
            print("error: setup cancelled", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print("configuration complete")
        return 0
    if command == "show":
        config = load_config(args.provider, project_root=project_root)
        print(f"provider: {config.provider_name}")
        print(f"model: {_effective_model(config)}")
        print(f"base_url: {_effective_base_url(config)}")
        print(f"api_key: {_effective_api_key_status(config)}")
        print(f"parallel_tool_calls: {_effective_parallel_tool_calls(config)}")
        print("config_files:")
        for path in config.loaded_config_paths:
            print(f"  - {path}")
        if not config.loaded_config_paths:
            print("  - <none>")
        return 0
    print(f"error: unknown config command: {command}", file=sys.stderr)
    return 2


def _effective_model(config) -> str:
    model = config.get_config_value("model") or config.get_env("ROOK_MODEL")
    return model or "<provider default>"


def _effective_base_url(config) -> str:
    base_url = config.get_provider_value("base_url", env="ROOK_BASE_URL")
    return base_url or "<provider default>"


def _effective_parallel_tool_calls(config) -> str:
    enabled = config.get_provider_bool(
        "parallel_tool_calls",
        env="ROOK_PARALLEL_TOOL_CALLS",
        default=False,
    )
    return "true" if enabled else "false"


def _effective_api_key_status(config) -> str:
    if config.provider_name == "ollama":
        return "not required"
    if config.provider_name in {"openai-compatible", "custom"}:
        key_env = config.get_provider_value("api_key_env") or "ROOK_API_KEY"
    else:
        preset = PROVIDER_PRESETS.get(config.provider_name)
        key_env = preset.api_key_env if preset is not None else "ROOK_API_KEY"
    if config.get_env(key_env):
        return f"environment ({key_env})"
    if read_api_key(key_env):
        return f"system credential ({key_env})"
    return f"missing ({key_env}; run `rook config setup`)"


def _benchmark_limits(max_tool_rounds: int | None) -> AgentLoopLimits:
    base = AgentLoopLimits.swe_lite()
    if max_tool_rounds is None:
        return base
    return base.with_max_tool_rounds(max_tool_rounds)


def run_repl(
    chat_runner: ChatRunnerLike,
    lines: Iterable[str] | None = None,
    *,
    auto_approve: bool = False,
) -> None:
    source = iter(lines) if lines is not None else _stdin_lines()
    pending = None
    for raw_line in source:
        line = raw_line.strip()
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break

        if pending is not None:
            if _pending_kind(pending) == "permission_confirmation":
                choice = _permission_choice_for_text(line, pending)
                if choice is None:
                    print(f"Unknown permission choice: {line}")
                    print(_permission_choice_help_text(pending))
                    print(_permission_options_text(pending))
                    continue
                line = choice
            response = chat_runner.resume_with_user_input(_pending_id(pending), line)
        else:
            response = chat_runner.run_user_turn(line)

        print(f"Rook> {response.content}")
        pending = getattr(chat_runner, "last_pending_input", None)
        while pending is not None and auto_approve and _pending_kind(pending) == "permission_confirmation":
            print("Auto-approve> allow_once")
            response = chat_runner.resume_with_user_input(_pending_id(pending), "allow_once")
            print(f"Rook> {response.content}")
            pending = getattr(chat_runner, "last_pending_input", None)

        if pending is not None:
            if _pending_kind(pending) == "permission_confirmation":
                print(_permission_options_text(pending))
            else:
                print(f"Permission> {_pending_question(pending)}")


def _stdin_lines():
    prompt = _create_prompt_session()
    if prompt is not None:
        while True:
            try:
                yield prompt.prompt("You> ")
            except (EOFError, KeyboardInterrupt):
                break
        return

    while True:
        try:
            yield input("You> ")
        except EOFError:
            break


def _create_prompt_session():
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
    except ImportError:
        return None
    return PromptSession(history=InMemoryHistory())


def _pending_id(pending: object) -> str:
    return str(getattr(pending, "id"))


def _pending_question(pending: object) -> str:
    return str(getattr(pending, "question", "需要用户输入。"))


def _pending_kind(pending: object) -> str:
    return str(getattr(pending, "kind", ""))


def _permission_choice_for_text(text: str, pending: object) -> str | None:
    normalized = text.strip().lower().replace(" ", "_")
    aliases = {
        "1": "deny",
        "n": "deny",
        "no": "deny",
        "deny": "deny",
        "2": "allow_once",
        "y": "allow_once",
        "yes": "allow_once",
        "allow": "allow_once",
        "once": "allow_once",
        "allow_once": "allow_once",
        "3": "allow_session_same_scope",
        "session": "allow_session_same_scope",
        "allow_session": "allow_session_same_scope",
        "allow_session_same_scope": "allow_session_same_scope",
        "4": "allow_always_same_scope",
        "always": "allow_always_same_scope",
        "allow_always": "allow_always_same_scope",
        "allow_always_same_scope": "allow_always_same_scope",
    }
    if normalized in aliases:
        return aliases[normalized]

    for index, option in enumerate(_permission_options(pending), start=1):
        option_id = _option_id(option)
        label = _option_label(option)
        values = {
            str(index).lower(),
            option_id.lower(),
            label.strip().lower().replace(" ", "_"),
        }
        if normalized in values:
            return option_id
    return None


def _permission_options_text(pending: object) -> str:
    question = _pending_question(pending)
    options = _permission_options(pending)
    option_lines = [
        f"  {index}. {_option_label(option)}"
        + (f" ({_option_id(option)})" if _option_id(option) != _option_label(option) else "")
        for index, option in enumerate(options, start=1)
    ]
    if not option_lines:
        option_lines = [
            "  1. Deny",
            "  2. Allow once",
            "  3. Allow for this session",
            "  4. Allow always for same scope",
        ]
    return "\n".join(
        [
            f"Permission> {question}",
            "Choose:",
            *option_lines,
        ]
    )


def _permission_choice_help_text(pending: object) -> str:
    count = len(_permission_options(pending)) or 3
    choices = ", ".join(str(index) for index in range(1, count + 1))
    return f"Please choose {choices}."


def _permission_options(pending: object) -> list[object]:
    return list(getattr(pending, "options", []) or [])


def _option_id(option: object) -> str:
    if isinstance(option, dict):
        return str(option.get("id") or option.get("label") or "")
    return str(getattr(option, "id", getattr(option, "label", "")))


def _option_label(option: object) -> str:
    if isinstance(option, dict):
        return str(option.get("label") or option.get("id") or "")
    return str(getattr(option, "label", getattr(option, "id", "")))


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed

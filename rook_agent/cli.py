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
from rook_agent.config.settings import default_global_config_path, project_config_path, render_default_config
from rook_agent.eval.adapter import RookCodingAgentAdapter
from rook_agent.eval.tasks import CodingTask


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Rook Coding Agent or its Rook Forge Skill governance commands."
    )
    subparsers = parser.add_subparsers(dest="command")
    config_parser = subparsers.add_parser("config", help="Inspect or initialize Rook configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("path", help="Show global and project config paths.")
    config_subparsers.add_parser("show", help="Show effective provider configuration without secrets.")
    init_parser = config_subparsers.add_parser("init", help="Create a starter global config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite the existing global config.")

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
        help="Run the message with the non-interactive benchmark adapter using bypass permissions.",
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
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "config":
        return run_config_command(args)
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
            app = create_cli_app(config)
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
            app = create_cli_app(config)
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
    """Run a single benchmark task with bypass permissions and repo-local tools."""

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
    if command == "show":
        config = load_config(args.provider, project_root=project_root)
        print(f"provider: {config.provider_name}")
        print(f"model: {_effective_model(config)}")
        print(f"base_url: {_effective_base_url(config)}")
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
        "3": "allow_always_same_scope",
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
            "  3. Allow always for same scope",
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

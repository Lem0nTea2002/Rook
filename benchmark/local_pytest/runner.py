"""Local pytest benchmark runner for Rook."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rook_agent.agent.loop_limits import AgentLoopLimits  # noqa: E402
from rook_agent.eval.adapter import RookCodingAgentAdapter  # noqa: E402
from rook_agent.eval.patch import collect_git_diff  # noqa: E402
from rook_agent.eval.tasks import CodingTask, CodingTaskResult  # noqa: E402

DEFAULT_TASKS = "benchmark/local_pytest/tasks.sample.jsonl"
DEFAULT_TEST_COMMAND = "python -m pytest -q"


@dataclass(frozen=True, slots=True)
class LocalPytestTask:
    id: str
    title: str
    files: dict[str, str]
    problem_statement: str
    test_command: str = DEFAULT_TEST_COMMAND
    repository: str = ""
    commit: str = ""
    source_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PytestResult:
    passed: bool
    returncode: int
    output: str


class LocalAgentAdapter(Protocol):
    def run_task(self, task: CodingTask) -> CodingTaskResult:
        ...


def load_tasks_jsonl(path: str | Path) -> list[LocalPytestTask]:
    tasks: list[LocalPytestTask] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            data = json.loads(line)
            tasks.append(
                LocalPytestTask(
                    id=str(data["id"]),
                    title=str(data.get("title") or data["id"]),
                    files={str(name): str(content) for name, content in dict(data["files"]).items()},
                    problem_statement=str(data["problem_statement"]),
                    test_command=str(data.get("test_command") or DEFAULT_TEST_COMMAND),
                    repository=str(data.get("repository") or ""),
                    commit=str(data.get("commit") or ""),
                    source_paths=tuple(
                        str(value)
                        for value in data.get("source_paths", [])
                    ),
                )
            )
    return tasks


def materialize_task_repo(task: LocalPytestTask, workdir: str | Path, *, force: bool = False) -> Path:
    repo = Path(workdir) / task.id
    if repo.exists():
        if not force:
            raise RuntimeError(f"Task repository already exists: {repo}. Use --force to recreate it.")
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    for relative_path, content in task.files.items():
        _write_task_file(repo, relative_path, content)
    _write_task_file(repo, ".gitignore", "__pycache__/\n*.py[cod]\n.pytest_cache/\n")
    _init_git_repo(repo)
    return repo


def run_pytest(repo: str | Path, command: str) -> PytestResult:
    result = subprocess.run(
        _split_command(command),
        cwd=Path(repo),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return PytestResult(
        passed=result.returncode == 0,
        returncode=result.returncode,
        output=result.stdout or "",
    )


def run_tasks(
    *,
    tasks: list[LocalPytestTask],
    workdir: str | Path,
    summary_out: str | Path,
    start_at: int = 1,
    max_tasks: int | None = None,
    provider_name: str | None = None,
    model_name: str = "rook-local-pytest",
    session_root: str | Path = ".rook-local-pytest",
    max_provider_calls_per_task: int = 20,
    max_tool_rounds_per_task: int = 20,
    max_turn_seconds_per_task: float = 300,
    force: bool = False,
    adapter: LocalAgentAdapter | None = None,
) -> list[dict[str, Any]]:
    selected = tasks[start_at - 1 :]
    if max_tasks is not None:
        selected = selected[:max_tasks]
    workdir_path = Path(workdir)
    workdir_path.mkdir(parents=True, exist_ok=True)
    agent = adapter or RookCodingAgentAdapter(
        model_name_or_path=model_name,
        provider_name=provider_name,
        session_root=session_root,
        limits=AgentLoopLimits(
            max_tool_rounds=max_tool_rounds_per_task,
            max_provider_calls=max_provider_calls_per_task,
            max_turn_seconds=max_turn_seconds_per_task,
            successful_verification_stop=True,
        ),
    )
    rows: list[dict[str, Any]] = []
    write_summary_json(summary_out, rows)
    for task in selected:
        rows.append(run_one_task(task=task, workdir=workdir_path, adapter=agent, force=force))
        write_summary_json(summary_out, rows)
    return rows


def run_one_task(
    *,
    task: LocalPytestTask,
    workdir: Path,
    adapter: LocalAgentAdapter,
    force: bool,
) -> dict[str, Any]:
    started_at = time.time()
    repo = materialize_task_repo(task, workdir, force=force)
    coding_task = CodingTask(
        instance_id=task.id,
        repo_path=repo,
        problem_statement=_build_problem_statement(task),
        metadata={
            "benchmark": "local_pytest",
            "title": task.title,
            "test_command": task.test_command,
            "repository": task.repository,
            "commit": task.commit,
            "source_paths": list(task.source_paths),
        },
    )
    result = adapter.run_task(coding_task)
    pytest_result = run_pytest(repo, task.test_command)
    return {
        "id": task.id,
        "title": task.title,
        "repo_path": str(repo),
        "passed": pytest_result.passed,
        "returncode": pytest_result.returncode,
        "elapsed_seconds": round(time.time() - started_at, 3),
        "test_command": task.test_command,
        "repository": task.repository,
        "commit": task.commit,
        "source_paths": list(task.source_paths),
        "pytest_output": pytest_result.output,
        "transcript_path": str(result.transcript_path) if result.transcript_path else None,
        "finish_reason": result.finish_reason,
        "context_metrics": result.context_metrics,
        "raw_response": result.raw_response,
        "model_patch": collect_git_diff(repo, include_untracked=True),
    }


def write_summary_json(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(list(rows), ensure_ascii=False, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=out.parent,
            prefix=f".{out.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(payload)
            temp.flush()
            temp_path = Path(temp.name)
        temp_path.replace(out)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Rook on small local pytest coding tasks.")
    parser.add_argument("--tasks", default=DEFAULT_TASKS, help="JSONL file containing local pytest tasks.")
    parser.add_argument("--workdir", required=True, help="Directory where task repositories are created.")
    parser.add_argument("--summary-out", default="runs/local-pytest-summary.json", help="JSON summary output path.")
    parser.add_argument(
        "--start-at",
        type=_positive_int,
        default=1,
        help="One-based task position to start from (default: 1).",
    )
    parser.add_argument("--max-tasks", type=_positive_int, default=None, help="Limit number of tasks.")
    parser.add_argument("--provider", default=None, help="Rook provider name. Defaults to app config.")
    parser.add_argument("--model-name", default="rook-local-pytest", help="Model name recorded in sessions.")
    parser.add_argument("--session-root", default=".rook-local-pytest", help="Directory for benchmark sessions.")
    parser.add_argument(
        "--max-provider-calls-per-task",
        type=_positive_int,
        default=20,
        help="Hard provider-call limit for each task (default: 20).",
    )
    parser.add_argument(
        "--max-tool-rounds-per-task",
        type=_positive_int,
        default=20,
        help="Hard tool-round limit for each task (default: 20).",
    )
    parser.add_argument(
        "--max-turn-seconds-per-task",
        type=_positive_float,
        default=300,
        help="Hard wall-clock limit for each task in seconds (default: 300).",
    )
    parser.add_argument("--force", action="store_true", help="Recreate existing task repositories.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rows = run_tasks(
            tasks=load_tasks_jsonl(args.tasks),
            workdir=args.workdir,
            summary_out=args.summary_out,
            start_at=args.start_at,
            max_tasks=args.max_tasks,
            provider_name=args.provider,
            model_name=args.model_name,
            session_root=args.session_root,
            max_provider_calls_per_task=args.max_provider_calls_per_task,
            max_tool_rounds_per_task=args.max_tool_rounds_per_task,
            max_turn_seconds_per_task=args.max_turn_seconds_per_task,
            force=args.force,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    passed = sum(1 for row in rows if row["passed"])
    print(f"Wrote local pytest benchmark summary: {args.summary_out}")
    print(f"Passed {passed}/{len(rows)}")
    return 0 if passed == len(rows) else 2


def _write_task_file(repo: Path, relative_path: str, content: str) -> None:
    path = (repo / relative_path).resolve()
    if repo.resolve() not in path.parents:
        raise RuntimeError(f"Task file path escapes repo: {relative_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_git_repo(repo: Path) -> None:
    _run_git(["init"], repo)
    _run_git(["config", "user.email", "benchmark@example.com"], repo)
    _run_git(["config", "user.name", "Benchmark"], repo)
    _run_git(["add", "-A"], repo)
    _run_git(["commit", "-m", "initial task"], repo)


def _run_git(args: list[str], repo: Path) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _split_command(command: str) -> list[str]:
    parts = command.split()
    if not parts:
        raise RuntimeError("test_command cannot be empty")
    if parts[:2] in (["python", "-m"], ["python3", "-m"]):
        parts[0] = sys.executable
    return parts


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _build_problem_statement(task: LocalPytestTask) -> str:
    return (
        f"Task: {task.title}\n\n"
        f"{task.problem_statement.strip()}\n\n"
        f"Validation command: {task.test_command}\n"
        "Edit the repository files until the validation command passes. Keep the fix minimal."
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

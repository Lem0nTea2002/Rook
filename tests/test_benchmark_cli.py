from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rook_agent.benchmarks.cli import run_benchmark_command
from rook_agent.cli import main


def test_main_dispatches_benchmark_without_creating_provider(tmp_path: Path) -> None:
    seen = []

    def benchmark_runner(args):
        seen.append(args)
        return 0

    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "benchmark",
            "recovery",
            "verify",
            "--catalog",
            str(tmp_path / "recovery.jsonl"),
        ],
        benchmark_runner=benchmark_runner,
    )

    assert exit_code == 0
    assert seen[0].benchmark_family == "recovery"
    assert seen[0].recovery_command == "verify"


def test_native_live_commands_parse_explicit_cost_authorization(
    tmp_path: Path,
) -> None:
    seen = []

    def benchmark_runner(args):
        seen.append(args)
        return 0

    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "benchmark",
            "native",
            "run",
            "--phase",
            "formal",
            "--validators",
            str(tmp_path / "validators.json"),
            "--allow-external",
            "--allow-costs",
        ],
        benchmark_runner=benchmark_runner,
    )

    assert exit_code == 0
    assert seen[0].native_command == "run"
    assert seen[0].phase == "formal"
    assert seen[0].allow_external is True
    assert seen[0].allow_costs is True


def test_native_live_command_fails_before_provider_without_double_authorization(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "benchmark",
            "native",
            "smoke",
            "--validators",
            str(tmp_path / "private.json"),
        ],
    )

    assert exit_code == 2
    assert "同时显式传入 --allow-external 和 --allow-costs" in capsys.readouterr().err


def test_memory_seed_validation_parses_explicit_bounded_live_command(
    tmp_path: Path,
) -> None:
    seen = []

    def benchmark_runner(args):
        seen.append(args)
        return 0

    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "benchmark",
            "memory",
            "seed-validate",
            "--cases",
            str(tmp_path / "recovery-seeds.json"),
            "--allow-external",
            "--allow-costs",
        ],
        benchmark_runner=benchmark_runner,
    )

    assert exit_code == 0
    assert seen[0].memory_command == "seed-validate"
    assert seen[0].allow_external is True
    assert seen[0].allow_costs is True


def test_memory_run_parses_two_targeted_pilot_tasks(tmp_path: Path) -> None:
    seen = []

    def benchmark_runner(args):
        seen.append(args)
        return 0

    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "benchmark",
            "memory",
            "run",
            "--phase",
            "pilot",
            "--validators",
            str(tmp_path / "validators.json"),
            "--task",
            "pylint-dev__pylint-7114",
            "--task",
            "pydata__xarray-3364",
            "--allow-external",
            "--allow-costs",
        ],
        benchmark_runner=benchmark_runner,
    )

    assert exit_code == 0
    assert seen[0].task == [
        "pylint-dev__pylint-7114",
        "pydata__xarray-3364",
    ]


def test_memory_run_parses_four_targeted_pilot_tasks(tmp_path: Path) -> None:
    seen = []

    def benchmark_runner(args):
        seen.append(args)
        return 0

    task_ids = [
        "pylint-dev__pylint-7114",
        "pydata__xarray-3364",
        "astropy__astropy-14182",
        "django__django-11620",
    ]
    arguments = [
        "--project",
        str(tmp_path),
        "benchmark",
        "memory",
        "run",
        "--phase",
        "pilot",
        "--validators",
        str(tmp_path / "validators.json"),
    ]
    for task_id in task_ids:
        arguments.extend(("--task", task_id))
    arguments.extend(("--allow-external", "--allow-costs"))

    assert main(arguments, benchmark_runner=benchmark_runner) == 0
    assert seen[0].task == task_ids


def test_memory_report_does_not_require_catalog_argument(
    monkeypatch,
    capsys,
) -> None:
    report = SimpleNamespace(valid=True, to_dict=lambda: {"valid": True})
    monkeypatch.setattr(
        "rook_agent.benchmarks.memory.write_memory_report",
        lambda root, experiment_id: (
            report,
            {
                "scorecard_json": "scorecard.json",
                "report_markdown": "report.md",
                "comparison_svg": "comparison.svg",
            },
        ),
    )
    args = SimpleNamespace(
        benchmark_family="memory",
        memory_command="report",
        root="artifacts",
        experiment_id="memory-pilot-1",
    )

    assert run_benchmark_command(args) == 0
    assert '"valid": true' in capsys.readouterr().out

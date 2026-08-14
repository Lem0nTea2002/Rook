import json
from pathlib import Path

import pytest

from rook_agent.eval.swebench import (
    SwebenchInstance,
    build_harness_command,
    build_parser,
    load_instances_jsonl,
    main,
    repo_path_for_instance,
    write_predictions_jsonl,
)
from rook_agent.eval.tasks import CodingTaskResult


def test_load_instances_jsonl_reads_minimal_swebench_fields(tmp_path: Path):
    path = tmp_path / "instances.jsonl"
    path.write_text(
        json.dumps(
            {
                "instance_id": "sympy__sympy-20590",
                "repo": "sympy/sympy",
                "base_commit": "abc123",
                "problem_statement": "Fix it.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    instances = load_instances_jsonl(path)

    assert instances == [
        SwebenchInstance(
            instance_id="sympy__sympy-20590",
            repo="sympy/sympy",
            base_commit="abc123",
            problem_statement="Fix it.",
        )
    ]


def test_repo_path_for_instance_uses_sanitized_instance_id(tmp_path: Path):
    instance = SwebenchInstance(
        instance_id="sympy__sympy-20590",
        repo="sympy/sympy",
        base_commit="abc123",
        problem_statement="Fix it.",
    )

    assert repo_path_for_instance(tmp_path, instance) == tmp_path / "sympy__sympy-20590"


def test_repo_path_for_instance_rejects_path_escape(tmp_path: Path):
    instance = SwebenchInstance(
        instance_id="../outside",
        repo="bad/repo",
        base_commit="abc123",
        problem_statement="Fix it.",
    )

    with pytest.raises(ValueError, match="outside repos_root"):
        repo_path_for_instance(tmp_path, instance)


def test_write_predictions_jsonl(tmp_path: Path):
    out = tmp_path / "predictions.jsonl"
    write_predictions_jsonl(
        out,
        [
            CodingTaskResult(
                instance_id="sympy__sympy-20590",
                model_name_or_path="rook",
                model_patch="diff --git a/a.py b/a.py\n",
            )
        ],
    )

    assert out.read_text(encoding="utf-8") == (
        '{"instance_id":"sympy__sympy-20590","model_name_or_path":"rook",'
        '"model_patch":"diff --git a/a.py b/a.py\\n"}\n'
    )


def test_parser_defaults_to_one_instance():
    args = build_parser().parse_args(
        [
            "--instances",
            "instances.jsonl",
            "--repos-root",
            "repos",
            "--out",
            "predictions.jsonl",
        ]
    )

    assert args.max_instances == 1
    assert args.model_name == "rook"


def test_parser_rejects_negative_max_instances():
    parser = build_parser()

    try:
        parser.parse_args(
            [
                "--instances",
                "instances.jsonl",
                "--repos-root",
                "repos",
                "--out",
                "predictions.jsonl",
                "--max-instances",
                "-1",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser error for negative max_instances")


def test_build_harness_command_for_swebench_lite():
    command = build_harness_command(
        predictions_path=Path("runs/rook_predictions.jsonl"),
        run_id="rook-smoke",
        max_workers=2,
    )

    assert command == [
        "python",
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        "princeton-nlp/SWE-bench_Lite",
        "--predictions_path",
        str(Path("runs/rook_predictions.jsonl")),
        "--max_workers",
        "2",
        "--run_id",
        "rook-smoke",
    ]


def test_main_can_print_harness_command(tmp_path: Path, monkeypatch, capsys):
    instances = tmp_path / "instances.jsonl"
    instances.write_text(
        json.dumps(
            {
                "instance_id": "sympy__sympy-20590",
                "repo": "sympy/sympy",
                "base_commit": "abc123",
                "problem_statement": "Fix it.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "predictions.jsonl"
    monkeypatch.setattr("rook_agent.eval.swebench.run_instances", lambda **kwargs: [])

    exit_code = main(
        [
            "--instances",
            str(instances),
            "--repos-root",
            str(tmp_path / "repos"),
            "--out",
            str(out),
            "--print-harness-command",
            "--run-id",
            "rook-smoke",
            "--max-workers",
            "2",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "python -m swebench.harness.run_evaluation "
        "--dataset_name princeton-nlp/SWE-bench_Lite "
        f"--predictions_path {out} "
        "--max_workers 2 "
        "--run_id rook-smoke\n"
    )

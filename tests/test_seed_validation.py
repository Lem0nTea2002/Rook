from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from rook_agent.benchmarks.seed_validation import (
    RecoverySeedCatalog,
    RecoverySeedRunner,
    _seed_session_parent,
    _result_reason,
    directory_fingerprint,
)
from rook_agent.eval.tasks import CodingTaskResult
from rook_agent.providers.base import ChatProvider
from rook_agent.providers.types import ChatRequest, ChatResponse


ROOT = Path(__file__).parents[1]
CATALOG = ROOT / "benchmark" / "memory" / "v1" / "recovery-seeds.json"


class _WrongProvider(ChatProvider):
    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    def complete(self, request: ChatRequest) -> ChatResponse:
        raise AssertionError("不应调用 Provider")


def test_recovery_seed_catalog_freezes_all_ten_fixtures() -> None:
    catalog = RecoverySeedCatalog.load(CATALOG)

    assert [case.seed_id for case in catalog.cases] == [
        "seed-01-neighbor-tests",
        "seed-02-resolve-path",
        "seed-03-project-entrypoint",
        "seed-04-semantic-invariant",
        "seed-05-random-determinism",
        "seed-06-version-compatibility",
        "seed-07-config-negative-path",
        "seed-08-state-cleanup",
        "seed-09-doctest-source",
        "seed-10-output-backends",
    ]
    assert all(
        directory_fingerprint(case.fixture) == case.fixture_sha256
        for case in catalog.cases
    )
    selected = catalog.select(
        ("seed-01-neighbor-tests", "seed-10-output-backends")
    )
    assert [case.seed_id for case in selected.cases] == [
        "seed-01-neighbor-tests",
        "seed-10-output-backends",
    ]


def test_recovery_seed_fingerprint_is_stable_across_platform_line_endings(
    tmp_path: Path,
) -> None:
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    (lf / "fixture.py").write_bytes(b"value = 1\n")
    (crlf / "fixture.py").write_bytes(b"value = 1\r\n")

    assert directory_fingerprint(lf) == directory_fingerprint(crlf)


def test_recovery_seed_fixtures_begin_with_real_test_failure() -> None:
    catalog = RecoverySeedCatalog.load(CATALOG)

    for case in catalog.cases:
        result = subprocess.run(
            (
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                *case.validation_args,
            ),
            cwd=case.fixture,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1, case.seed_id


def test_seed_result_requires_verified_exact_scope_and_one_opportunity() -> None:
    result = CodingTaskResult(
        instance_id="seed-01-neighbor-tests",
        model_name_or_path="deepseek-v4-flash",
        model_patch="diff --git a/src/rook_seed/slugify.py b/src/rook_seed/slugify.py\n",
    )

    assert (
        _result_reason(
            result=result,
            final_exit_code=0,
            changed_paths=("src/rook_seed/slugify.py",),
            allowed_paths=("src/rook_seed/slugify.py",),
            opportunity_count=1,
        )
        == "passed"
    )
    assert (
        _result_reason(
            result=result,
            final_exit_code=0,
            changed_paths=("tests/test_slugify.py",),
            allowed_paths=("src/rook_seed/slugify.py",),
            opportunity_count=1,
        )
        == "unexpected_changed_paths"
    )
    assert (
        _result_reason(
            result=result,
            final_exit_code=0,
            changed_paths=("src/rook_seed/slugify.py",),
            allowed_paths=("src/rook_seed/slugify.py",),
            opportunity_count=0,
        )
        == "recovery_opportunity_missing"
    )


def test_seed_runner_rejects_provider_or_model_fallback() -> None:
    with pytest.raises(ValueError, match="禁止回退"):
        RecoverySeedRunner(_WrongProvider())


def test_seed_runner_disables_todo_for_bounded_micro_tasks(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class _Provider(ChatProvider):
        @property
        def name(self) -> str:
            return "deepseek"

        @property
        def model(self) -> str:
            return "deepseek-v4-flash"

        def complete(self, request: ChatRequest) -> ChatResponse:
            raise AssertionError("不应调用真实 Provider")

    class _Adapter:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run_task(self, task):
            return CodingTaskResult(
                instance_id=task.instance_id,
                model_name_or_path="deepseek-v4-flash",
                model_patch="",
            )

    monkeypatch.setattr("rook_agent.benchmarks.seed_validation.RookCodingAgentAdapter", _Adapter)
    case = RecoverySeedCatalog.load(CATALOG).cases[0]

    RecoverySeedRunner(_Provider()).run(
        case=case,
        workspace=tmp_path / "workspace",
        session_root=tmp_path / "sessions",
    )

    assert captured["include_todo_tool"] is False


def test_seed_session_parent_avoids_repeating_case_id(tmp_path: Path) -> None:
    experiment_root = tmp_path / (
        "memory-seed-20260801T045043810799Z-683eaa23"
    )

    parent = _seed_session_parent(experiment_root)
    transcript = (
        parent
        / "seed-03-project-entrypoint"
        / "sessions"
        / "seed-03-project-entrypoint.jsonl"
    )

    assert parent == experiment_root / "_sessions"
    assert str(transcript).count("seed-03-project-entrypoint") == 2

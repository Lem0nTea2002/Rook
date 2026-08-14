"""Deterministic, cost-free GitHub pull-request gate for Rook Forge assets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.bundles import load_skill_bundle
from rook_agent.evalops.skills import render_skill
from rook_agent.evalops.suites import load_eval_suite
from rook_agent.execution.repository import FullRepoTaskCatalog


_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_RELEVANT_PREFIXES = (
    "evals/",
    "rook_agent/agent/",
    "rook_agent/context/",
    "rook_agent/evalops/",
    "rook_agent/execution/",
    "rook_agent/skills/",
    "benchmark/full_repo/",
)


def evaluate_pr_gate(
    project_root: str | Path,
    *,
    base_ref: str | None = None,
    head_ref: str | None = None,
    changed_paths: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Validate every governed asset when a PR changes a relevant boundary."""

    root = Path(project_root).resolve()
    paths = (
        _git_changed_paths(root, base_ref=base_ref, head_ref=head_ref)
        if changed_paths is None
        else _normalize_changed_paths(changed_paths)
    )
    applicable = any(
        path.startswith(_RELEVANT_PREFIXES)
        for path in paths
    )
    summary = {
        "candidate_locked_suites": 0,
        "candidates": 0,
        "provenance_files": 0,
        "suites": 0,
        "full_repo_tasks": 0,
        "full_repo_repositories": 0,
    }
    failures: list[dict[str, str]] = []
    checks: list[dict[str, object]] = []

    if applicable:
        candidate_hashes = _validate_candidates(
            root,
            summary=summary,
            failures=failures,
            checks=checks,
        )
        _validate_suites(
            root,
            candidate_hashes=candidate_hashes,
            summary=summary,
            failures=failures,
            checks=checks,
        )
        _validate_provenance_files(
            root,
            summary=summary,
            failures=failures,
            checks=checks,
        )
        _validate_full_repo_catalog(
            root,
            summary=summary,
            failures=failures,
            checks=checks,
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "gate": "rook_forge_pr_gate",
        "status": "failed" if failures else "passed",
        "applicable": applicable,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_paths": list(paths),
        "external_calls": False,
        "model_costs": False,
        "summary": summary,
        "checks": checks,
        "failures": failures,
    }
    report["fingerprint"] = stable_json_hash(report, length=32)
    return report


def write_pr_gate_report(
    report: dict[str, Any],
    output_path: str | Path,
    *,
    allowed_root: str | Path,
) -> Path:
    """Atomically write the gate report inside the explicitly allowed root."""

    root = Path(allowed_root).resolve()
    output = Path(output_path)
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    if output != root and root not in output.parents:
        raise ValueError("PR gate report path escapes the project root")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    )
    handle, raw_temp = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return output


def _git_changed_paths(
    root: Path,
    *,
    base_ref: str | None,
    head_ref: str | None,
) -> tuple[str, ...]:
    if not base_ref or not head_ref:
        raise ValueError("PR gate requires both base_ref and head_ref")
    for label, value in (("base_ref", base_ref), ("head_ref", head_ref)):
        if value.startswith("-") or len(value) > 200:
            raise ValueError(f"invalid {label}")
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            f"{base_ref}...{head_ref}",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.strip() or "git diff failed"
        raise ValueError(f"unable to resolve PR diff: {diagnostic}")
    return _normalize_changed_paths(tuple(result.stdout.splitlines()))


def _normalize_changed_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in paths:
        value = raw.strip().replace("\\", "/")
        if not value:
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("changed path escapes the repository")
        normalized.add(path.as_posix())
    return tuple(sorted(normalized))


def _validate_candidates(
    root: Path,
    *,
    summary: dict[str, int],
    failures: list[dict[str, str]],
    checks: list[dict[str, object]],
) -> set[str]:
    hashes: set[str] = set()
    candidate_root = root / "evals" / "candidates"
    paths = (
        sorted(candidate_root.rglob("*.toml"))
        if candidate_root.is_dir()
        else []
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            bundle = load_skill_bundle(path)
            content_hash = hashlib.sha256(
                render_skill(bundle).encode("utf-8")
            ).hexdigest()
        except Exception as exc:
            _failure(
                failures,
                code="candidate_invalid",
                path=relative,
                detail=str(exc),
            )
            continue
        hashes.add(content_hash)
        summary["candidates"] += 1
    checks.append(
        {
            "name": "strict_candidates",
            "status": "passed" if not any(
                item["code"] == "candidate_invalid"
                for item in failures
            ) else "failed",
            "count": summary["candidates"],
        }
    )
    return hashes


def _validate_suites(
    root: Path,
    *,
    candidate_hashes: set[str],
    summary: dict[str, int],
    failures: list[dict[str, str]],
    checks: list[dict[str, object]],
) -> None:
    suite_root = root / "evals" / "suites"
    paths = (
        sorted(suite_root.rglob("suite.toml"))
        if suite_root.is_dir()
        else []
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            suite = load_eval_suite(path)
        except Exception as exc:
            _failure(
                failures,
                code="suite_invalid",
                path=relative,
                detail=str(exc),
            )
            continue
        summary["suites"] += 1
        if suite.candidate_content_hash is None:
            continue
        if suite.candidate_content_hash not in candidate_hashes:
            _failure(
                failures,
                code="candidate_lock_unresolved",
                path=relative,
                detail=suite.candidate_content_hash,
            )
            continue
        summary["candidate_locked_suites"] += 1
    checks.append(
        {
            "name": "strict_suites_and_candidate_locks",
            "status": "passed" if not any(
                item["code"] in {"suite_invalid", "candidate_lock_unresolved"}
                for item in failures
            ) else "failed",
            "count": summary["suites"],
            "candidate_locked": summary["candidate_locked_suites"],
        }
    )


def _validate_provenance_files(
    root: Path,
    *,
    summary: dict[str, int],
    failures: list[dict[str, str]],
    checks: list[dict[str, object]],
) -> None:
    suite_root = root / "evals" / "suites"
    paths = (
        sorted(suite_root.rglob("PROVENANCE.json"))
        if suite_root.is_dir()
        else []
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            _failure(
                failures,
                code="provenance_invalid",
                path=relative,
                detail=str(exc),
            )
            continue
        if not isinstance(raw, dict):
            _failure(
                failures,
                code="provenance_invalid",
                path=relative,
                detail="root must be an object",
            )
            continue
        summary["provenance_files"] += 1
        _validate_repository_pins(raw, relative=relative, failures=failures)
        for item in _provenance_fixture_items(raw):
            _validate_provenance_fixture(
                path.parent,
                item,
                provenance_path=relative,
                failures=failures,
            )
    checks.append(
        {
            "name": "provenance_pins_and_fixture_hashes",
            "status": "passed" if not any(
                item["code"].startswith("provenance_")
                for item in failures
            ) else "failed",
            "count": summary["provenance_files"],
        }
    )


def _validate_repository_pins(
    raw: dict[str, object],
    *,
    relative: str,
    failures: list[dict[str, str]],
) -> None:
    repositories = raw.get("repositories")
    records = (
        repositories
        if isinstance(repositories, list)
        else [raw]
    )
    for record in records:
        if not isinstance(record, dict):
            _failure(
                failures,
                code="provenance_pin_invalid",
                path=relative,
                detail="repository pin must be an object",
            )
            continue
        repository = record.get("repository")
        commit = record.get("commit")
        license_name = record.get("license")
        if (
            not isinstance(repository, str)
            or not repository.startswith("https://github.com/")
            or not isinstance(commit, str)
            or _HEX_40.fullmatch(commit) is None
            or not isinstance(license_name, str)
            or not license_name
        ):
            _failure(
                failures,
                code="provenance_pin_invalid",
                path=relative,
                detail="repository, 40-char commit, and license are required",
            )


def _provenance_fixture_items(raw: dict[str, object]) -> list[dict[str, object]]:
    fixture_files = raw.get("fixture_files")
    files = fixture_files if isinstance(fixture_files, list) else raw.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _validate_provenance_fixture(
    provenance_root: Path,
    item: dict[str, object],
    *,
    provenance_path: str,
    failures: list[dict[str, str]],
) -> None:
    fixture_ref = item.get("fixture")
    if not isinstance(fixture_ref, str) or not fixture_ref:
        _failure(
            failures,
            code="provenance_fixture_invalid",
            path=provenance_path,
            detail="fixture path is required",
        )
        return
    fixture = (provenance_root / fixture_ref).resolve()
    root = provenance_root.resolve()
    if root not in fixture.parents or not fixture.is_file() or fixture.is_symlink():
        _failure(
            failures,
            code="provenance_fixture_invalid",
            path=provenance_path,
            detail=fixture_ref,
        )
        return
    content = fixture.read_bytes()
    expected_sha256 = item.get("sha256", item.get("fixture_sha256"))
    if isinstance(expected_sha256, str):
        if (
            _HEX_64.fullmatch(expected_sha256) is None
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            _failure(
                failures,
                code="provenance_hash_mismatch",
                path=provenance_path,
                detail=fixture_ref,
            )
        return
    expected_blob = item.get("git_blob_sha1")
    if item.get("transformation") == "none" and isinstance(expected_blob, str):
        actual_blob = hashlib.sha1(  # noqa: S324 - Git blob identity.
            f"blob {len(content)}\0".encode() + content
        ).hexdigest()
        if _HEX_40.fullmatch(expected_blob) is None or actual_blob != expected_blob:
            _failure(
                failures,
                code="provenance_hash_mismatch",
                path=provenance_path,
                detail=fixture_ref,
            )
        return
    _failure(
        failures,
        code="provenance_hash_missing",
        path=provenance_path,
        detail=fixture_ref,
    )


def _validate_full_repo_catalog(
    root: Path,
    *,
    summary: dict[str, int],
    failures: list[dict[str, str]],
    checks: list[dict[str, object]],
) -> None:
    catalog_path = (
        root
        / "benchmark"
        / "full_repo"
        / "tasks.swebench-lite-24.jsonl"
    )
    provenance_path = root / "benchmark" / "full_repo" / "PROVENANCE.json"
    if not catalog_path.exists() and not provenance_path.exists():
        return
    try:
        catalog = FullRepoTaskCatalog.load(catalog_path)
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if not isinstance(provenance, dict):
            raise ValueError("full-repo provenance root must be an object")
        catalog_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        if provenance.get("catalog_sha256") != catalog_hash:
            raise ValueError("full-repo catalog hash does not match provenance")
        task_ids = [task.task_id for task in catalog.tasks]
        if provenance.get("task_ids") != task_ids:
            raise ValueError("full-repo task ids do not match provenance")
        selection = provenance.get("selection")
        if (
            not isinstance(selection, dict)
            or selection.get("total_tasks") != len(catalog.tasks)
        ):
            raise ValueError("full-repo task count does not match provenance")
        repositories = {task.repository for task in catalog.tasks}
        summary["full_repo_tasks"] = len(catalog.tasks)
        summary["full_repo_repositories"] = len(repositories)
    except Exception as exc:
        _failure(
            failures,
            code="full_repo_catalog_invalid",
            path="benchmark/full_repo",
            detail=str(exc),
        )
    checks.append(
        {
            "name": "full_repo_task_catalog",
            "status": "failed" if any(
                item["code"] == "full_repo_catalog_invalid"
                for item in failures
            ) else "passed",
            "count": summary["full_repo_tasks"],
            "repositories": summary["full_repo_repositories"],
        }
    )


def _failure(
    failures: list[dict[str, str]],
    *,
    code: str,
    path: str,
    detail: str,
) -> None:
    failures.append(
        {
            "code": code,
            "path": path,
            "detail": detail[:500],
        }
    )


__all__ = [
    "evaluate_pr_gate",
    "write_pr_gate_report",
]

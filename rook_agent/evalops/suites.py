"""版本化 EvalSuite TOML 的严格加载器。"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.models import (
    CaseCategory,
    EvalCase,
    EvalSuite,
    EvaluatorSpec,
    NetworkPolicy,
    PromotionPolicyConfig,
    plain_data,
)


_SUITE_FIELDS = {"id", "version", "policy", "cases"}
_CASE_FIELDS = {"id", "category", "task", "fixture", "evaluator", "timeout_seconds", "network"}


def load_eval_suite(path: str | Path) -> EvalSuite:
    """加载并验证一个版本化 suite manifest。"""

    manifest = Path(path).resolve()
    if not manifest.is_file():
        raise ValueError(f"suite manifest does not exist or is not a file: {manifest}")
    raw = _load_toml(manifest, context="suite manifest")
    _reject_unknown(raw, allowed=_SUITE_FIELDS, context="suite manifest")

    suite_id = _require_string(raw, "id", context="suite manifest")
    version = _require_string(raw, "version", context="suite manifest")
    policy_ref = _require_string(raw, "policy", context="suite manifest")
    raw_cases = _require_list(raw, "cases", context="suite manifest")
    if not raw_cases:
        raise ValueError("suite manifest field 'cases' must contain at least one case")

    cases_and_content = [_load_case(manifest.parent, value, index=index) for index, value in enumerate(raw_cases)]
    cases = tuple(item[0] for item in cases_and_content)
    _require_unique(case.id for case in cases)
    policy = _load_policy(manifest.parent, policy_ref)

    fingerprint = stable_json_hash(
        {
            "manifest": plain_data(raw),
            "case_content": [item[1] for item in cases_and_content],
            "policy_content": policy.fingerprint,
        },
        length=32,
    )
    return EvalSuite(
        id=suite_id,
        version=version,
        cases=cases,
        policy=policy,
        manifest_path=manifest,
        fingerprint=fingerprint,
    )


def _load_case(root: Path, value: object, *, index: int) -> tuple[EvalCase, Mapping[str, object]]:
    context = f"case at index {index}"
    raw = _require_mapping(value, context=context)
    _reject_unknown(raw, allowed=_CASE_FIELDS, context=context)

    case_id = _require_string(raw, "id", context=context)
    category_value = _require_string(raw, "category", context=context)
    try:
        category = CaseCategory(category_value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in CaseCategory)
        raise ValueError(f"invalid case category {category_value!r}; expected one of: {allowed}") from error

    network_value = _require_string(raw, "network", context=context)
    try:
        network_policy = NetworkPolicy(network_value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in NetworkPolicy)
        raise ValueError(f"invalid network policy {network_value!r}; expected one of: {allowed}") from error

    timeout_seconds = raw.get("timeout_seconds")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError(f"{context} field 'timeout_seconds' must be a positive integer")

    task_ref = _require_string(raw, "task", context=context)
    task_path = _resolve_under(root, task_ref, label=f"case {case_id!r} task", root_label="suite root")
    if not task_path.is_file():
        raise ValueError(f"case {case_id!r} task does not exist or is not a file: {task_path}")
    task = task_path.read_text(encoding="utf-8")

    fixture_ref = _require_string(raw, "fixture", context=context)
    fixture = _resolve_under(root, fixture_ref, label=f"case {case_id!r} fixture", root_label="suite root")
    if not fixture.is_dir():
        raise ValueError(f"case {case_id!r} fixture does not exist or is not a directory: {fixture}")

    evaluator_raw = _require_mapping(raw.get("evaluator"), context=f"case {case_id!r} evaluator")
    evaluator, evaluator_content = _load_evaluator(root, evaluator_raw, case_id=case_id)
    fixture_content = _fixture_content(fixture)

    case = EvalCase(
        id=case_id,
        category=category,
        task=task,
        fixture=fixture,
        evaluator=evaluator,
        timeout_seconds=timeout_seconds,
        network_policy=network_policy,
    )
    content = {
        "id": case_id,
        "task_sha256": _file_hash(task_path),
        "fixture_tree": fixture_content,
        "evaluator": evaluator_content,
    }
    return case, content


def _load_evaluator(
    root: Path,
    raw: Mapping[str, object],
    *,
    case_id: str,
) -> tuple[EvaluatorSpec, Mapping[str, object]]:
    kind = _require_string(raw, "kind", context=f"case {case_id!r} evaluator")
    options = {key: value for key, value in raw.items() if key != "kind"}
    referenced_files: list[Mapping[str, object]] = []

    if kind == "command":
        command = options.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError(f"case {case_id!r} command evaluator field 'command' must be a non-empty string list")
        for position, token in enumerate(command):
            if not _is_command_path(token, executable=position == 0):
                continue
            reference = _resolve_under(
                root,
                token,
                label=f"case {case_id!r} evaluator command path",
                root_label="suite root",
            )
            if not reference.is_file():
                raise ValueError(f"case {case_id!r} evaluator path does not exist or is not a file: {reference}")
            referenced_files.append(
                {
                    "position": position,
                    "reference": token,
                    "sha256": _file_hash(reference),
                }
            )

    evaluator = EvaluatorSpec(kind=kind, options=options)
    content = {
        "config": plain_data(raw),
        "referenced_files": referenced_files,
    }
    return evaluator, content


def _load_policy(suite_root: Path, reference: str) -> PromotionPolicyConfig:
    evals_root = next(
        (candidate for candidate in (suite_root, *suite_root.parents) if candidate.name == "evals"),
        None,
    )
    if evals_root is None:
        raise ValueError(f"suite root has no evals ancestor: {suite_root}")

    policy_root = (evals_root / "policies").resolve()
    policy_path = _resolve_under(
        policy_root,
        (suite_root / reference).resolve(),
        label="suite policy",
        root_label="policy root",
    )
    if not policy_path.is_file():
        raise ValueError(f"suite policy does not exist or is not a file: {policy_path}")

    raw = _load_toml(policy_path, context="promotion policy")
    version = _require_string(raw, "version", context="promotion policy")
    data = {key: value for key, value in raw.items() if key != "version"}
    fingerprint = stable_json_hash(
        {
            "data": plain_data(raw),
            "content_sha256": _file_hash(policy_path),
        },
        length=32,
    )
    return PromotionPolicyConfig(
        source=policy_path,
        version=version,
        data=data,
        fingerprint=fingerprint,
    )


def _fixture_content(root: Path) -> list[Mapping[str, object]]:
    content: list[Mapping[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"fixture contains unsupported symbolic link: {relative}")
        if path.is_dir():
            content.append({"path": relative, "kind": "directory"})
        elif path.is_file():
            content.append({"path": relative, "kind": "file", "sha256": _file_hash(path)})
        else:
            raise ValueError(f"fixture contains unsupported filesystem entry: {relative}")
    return content


def _resolve_under(root: Path, reference: str | Path, *, label: str, root_label: str) -> Path:
    resolved_root = root.resolve()
    candidate = Path(reference)
    resolved = candidate.resolve() if candidate.is_absolute() else (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"{label} escapes {root_label}: {reference}")
    return resolved


def _is_command_path(value: str, *, executable: bool) -> bool:
    if not value or value.startswith("-"):
        return False
    path = Path(value)
    if executable:
        return value.startswith(".") or "/" in value or "\\" in value
    return path.is_absolute() or value.startswith(".") or "/" in value or "\\" in value or bool(path.suffix)


def _load_toml(path: Path, *, context: str) -> Mapping[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load {context} {path}: {error}") from error


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_unknown(raw: Mapping[str, object], *, allowed: set[str], context: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{context} has unknown fields: {', '.join(unknown)}")


def _require_mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a table")
    return value


def _require_string(raw: Mapping[str, object], key: str, *, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} field {key!r} must be a non-empty string")
    return value


def _require_list(raw: Mapping[str, object], key: str, *, context: str) -> list[object]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{context} field {key!r} must be a list")
    return value


def _require_unique(case_ids: Iterable[str]) -> None:
    seen: set[str] = set()
    for case_id in case_ids:
        if case_id in seen:
            raise ValueError(f"duplicate case id: {case_id}")
        seen.add(case_id)

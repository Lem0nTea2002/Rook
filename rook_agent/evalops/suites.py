"""版本化 EvalSuite TOML 的严格加载器。"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

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


_SUITE_FIELDS = {"id", "version", "policy", "candidate_content_hash", "cases"}
_CASE_FIELDS = {"id", "category", "task", "fixture", "evaluator", "timeout_seconds", "network"}
_EVALUATOR_KINDS = {"command", "file_state", "trajectory", "composite", "llm_judge"}
_COMMAND_EVALUATOR_FIELDS = {"kind", "command", "timeout_seconds"}
_FILE_STATE_EVALUATOR_FIELDS = {
    "kind",
    "required_files",
    "forbidden_files",
    "expected_text",
    "expected_sha256",
}
_TRAJECTORY_EVALUATOR_FIELDS = {
    "kind",
    "required_tools",
    "forbidden_tools",
    "required_successful_tools",
    "require_trace_complete",
}
_COMPOSITE_EVALUATOR_FIELDS = {"kind", "children"}
_LLM_JUDGE_EVALUATOR_FIELDS = {"kind", "rubric", "max_trace_chars", "max_tokens"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


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
    candidate_content_hash = raw.get("candidate_content_hash")
    if candidate_content_hash is not None and (
        not isinstance(candidate_content_hash, str)
        or _SHA256.fullmatch(candidate_content_hash) is None
    ):
        raise ValueError(
            "suite manifest field 'candidate_content_hash' must be lowercase SHA-256 hex"
        )
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
        candidate_content_hash=candidate_content_hash,
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
    evaluator, evaluator_content = _load_evaluator(root, evaluator_raw, case_id=case_id, fixture=fixture)
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
    fixture: Path,
    depth: int = 0,
) -> tuple[EvaluatorSpec, Mapping[str, object]]:
    context = f"case {case_id!r} evaluator"
    kind = _require_string(raw, "kind", context=context)
    if kind not in _EVALUATOR_KINDS:
        allowed = ", ".join(sorted(_EVALUATOR_KINDS))
        raise ValueError(f"unsupported evaluator kind {kind!r}; expected one of: {allowed}")

    if kind == "command":
        return _load_command_evaluator(root, raw, case_id=case_id, fixture=fixture)
    options: dict[str, object]
    if kind == "file_state":
        _reject_unknown(raw, allowed=_FILE_STATE_EVALUATOR_FIELDS, context=context)
        required = _workspace_path_list(raw, "required_files", context=context)
        forbidden = _workspace_path_list(raw, "forbidden_files", context=context)
        expected_text = _workspace_path_mapping(raw, "expected_text", context=context)
        expected_sha256 = _workspace_path_mapping(raw, "expected_sha256", context=context)
        invalid_hashes = sorted(
            path for path, value in expected_sha256.items() if _SHA256.fullmatch(value) is None
        )
        if invalid_hashes:
            raise ValueError(f"{context} expected_sha256 values must be lowercase SHA-256 hex")
        if set(required) & set(forbidden):
            raise ValueError(f"{context} cannot require and forbid the same workspace path")
        if not (required or forbidden or expected_text or expected_sha256):
            raise ValueError(f"{context} file_state requires at least one assertion")
        options = {
            "required_files": required,
            "forbidden_files": forbidden,
            "expected_text": expected_text,
            "expected_sha256": expected_sha256,
        }
    elif kind == "trajectory":
        _reject_unknown(raw, allowed=_TRAJECTORY_EVALUATOR_FIELDS, context=context)
        required_tools = _string_list(raw, "required_tools", context=context)
        forbidden_tools = _string_list(raw, "forbidden_tools", context=context)
        options = {
            "required_tools": required_tools,
            "forbidden_tools": forbidden_tools,
            "required_successful_tools": _string_list(
                raw, "required_successful_tools", context=context
            ),
            "require_trace_complete": _optional_bool(
                raw, "require_trace_complete", default=True, context=context
            ),
        }
        if set(required_tools) & set(forbidden_tools):
            raise ValueError(f"{context} cannot require and forbid the same tool")
    elif kind == "llm_judge":
        _reject_unknown(raw, allowed=_LLM_JUDGE_EVALUATOR_FIELDS, context=context)
        rubric = _require_string(raw, "rubric", context=context)
        if len(rubric) > 4000:
            raise ValueError(f"{context} rubric must not exceed 4000 characters")
        options = {
            "rubric": rubric,
            "max_trace_chars": _bounded_positive_int(
                raw, "max_trace_chars", default=8000, maximum=20000, context=context
            ),
            "max_tokens": _bounded_positive_int(
                raw, "max_tokens", default=256, maximum=256, context=context
            ),
        }
    else:
        if depth >= 1:
            raise ValueError(f"{context} does not allow a nested composite evaluator")
        _reject_unknown(raw, allowed=_COMPOSITE_EVALUATOR_FIELDS, context=context)
        children_raw = _require_list(raw, "children", context=context)
        if not children_raw:
            raise ValueError(f"{context} composite requires at least one child")
        if len(children_raw) > 16:
            raise ValueError(f"{context} composite supports at most 16 children")
        loaded_children = [
            _load_evaluator(
                root,
                _require_mapping(child, context=f"{context} child at index {index}"),
                case_id=case_id,
                fixture=fixture,
                depth=depth + 1,
            )
            for index, child in enumerate(children_raw)
        ]
        child_specs = tuple(child[0] for child in loaded_children)
        judge_positions = [index for index, child in enumerate(child_specs) if child.kind == "llm_judge"]
        if len(judge_positions) > 1:
            raise ValueError(f"{context} composite supports at most one LLM judge")
        if judge_positions and judge_positions[0] != len(child_specs) - 1:
            raise ValueError(f"{context} LLM judge must be the last child")
        options = {"children": child_specs}
        evaluator = EvaluatorSpec(kind=kind, options=options)
        return evaluator, {
            "config": plain_data(raw),
            "children": [child[1] for child in loaded_children],
        }

    evaluator = EvaluatorSpec(kind=kind, options=options)
    return evaluator, {"config": plain_data(raw), "referenced_files": []}


def _load_command_evaluator(
    root: Path,
    raw: Mapping[str, object],
    *,
    case_id: str,
    fixture: Path,
) -> tuple[EvaluatorSpec, Mapping[str, object]]:
    context = f"case {case_id!r} evaluator"
    _reject_unknown(raw, allowed=_COMMAND_EVALUATOR_FIELDS, context=context)

    command = raw.get("command")
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item for item in command
    ):
        raise ValueError(f"{context} command evaluator field 'command' must be a non-empty string list")
    resolved_command: list[str] = []
    referenced_files: list[Mapping[str, object]] = []
    for position, token in enumerate(command):
        if not _is_command_path(token, executable=position == 0):
            resolved_command.append(token)
            continue
        reference = _resolve_under(
            root,
            token,
            label=f"case {case_id!r} evaluator command path",
            root_label="suite root",
        )
        if reference == fixture or reference.is_relative_to(fixture):
            raise ValueError(f"case {case_id!r} evaluator path is inside fixture: {reference}")
        if not reference.is_file():
            raise ValueError(f"case {case_id!r} evaluator path does not exist or is not a file: {reference}")
        resolved_command.append(str(reference))
        referenced_files.append(
            {
                "position": position,
                "reference": token,
                "sha256": _file_hash(reference),
            }
        )

    evaluator = EvaluatorSpec(
        kind="command",
        options={
            "command": tuple(resolved_command),
            "timeout_seconds": _bounded_positive_int(
                raw, "timeout_seconds", default=30, maximum=300, context=context
            ),
        },
    )
    content = {
        "config": plain_data(raw),
        "referenced_files": referenced_files,
    }
    return evaluator, content


def _string_list(raw: Mapping[str, object], key: str, *, context: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{context} field {key!r} must be a string list")
    if len(set(value)) != len(value):
        raise ValueError(f"{context} field {key!r} must not contain duplicates")
    return tuple(value)


def _workspace_path_list(
    raw: Mapping[str, object], key: str, *, context: str
) -> tuple[str, ...]:
    return tuple(
        _normalize_workspace_path(value, context=f"{context} field {key!r}")
        for value in _string_list(raw, key, context=context)
    )


def _workspace_path_mapping(
    raw: Mapping[str, object], key: str, *, context: str
) -> Mapping[str, str]:
    value = raw.get(key, {})
    if not isinstance(value, Mapping) or any(
        not isinstance(path, str) or not isinstance(expected, str)
        for path, expected in value.items()
    ):
        raise ValueError(f"{context} field {key!r} must be a string-to-string table")
    normalized: dict[str, str] = {}
    for path, expected in value.items():
        checked = _normalize_workspace_path(path, context=f"{context} field {key!r}")
        if checked in normalized:
            raise ValueError(f"{context} field {key!r} contains duplicate workspace paths")
        normalized[checked] = expected
    return normalized


def _normalize_workspace_path(value: str, *, context: str) -> str:
    candidate = Path(value)
    if (
        not value
        or candidate.is_absolute()
        or candidate.drive
        or candidate == Path(".")
        or ".." in candidate.parts
    ):
        raise ValueError(f"{context} contains invalid workspace path: {value!r}")
    return candidate.as_posix()


def _optional_bool(
    raw: Mapping[str, object], key: str, *, default: bool, context: str
) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{context} field {key!r} must be a boolean")
    return value


def _bounded_positive_int(
    raw: Mapping[str, object],
    key: str,
    *,
    default: int,
    maximum: int,
    context: str,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise ValueError(f"{context} field {key!r} must be an integer between 1 and {maximum}")
    return value


def _load_policy(suite_root: Path, reference: str) -> PromotionPolicyConfig:
    evals_root = next(
        (candidate for candidate in (suite_root, *suite_root.parents) if candidate.name == "evals"),
        None,
    )
    if evals_root is None:
        raise ValueError(f"suite root has no evals ancestor: {suite_root}")

    unresolved_policy_root = evals_root / "policies"
    if unresolved_policy_root.is_symlink():
        raise ValueError(f"policy root must not be a symbolic link: {unresolved_policy_root}")
    resolved_evals_root = evals_root.resolve()
    policy_root = unresolved_policy_root.resolve()
    if not policy_root.is_relative_to(resolved_evals_root):
        raise ValueError(f"policy root escapes evals root: {unresolved_policy_root}")
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

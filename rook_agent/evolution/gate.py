"""Deterministic governance for model-produced Forge skill deltas."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
import math
from pathlib import Path
import posixpath
import re
import unicodedata

from rook_agent.evolution.models import (
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    EvolutionScope,
    GateDecision,
    GateStatus,
    SkillDelta,
    TaskTrace,
)


SCHEMA_INVALID = "schema_invalid"
EVIDENCE_REF_MISSING = "evidence_ref_missing"
EVIDENCE_REF_OUTSIDE_SEGMENT = "evidence_ref_outside_segment"
EXECUTABLE_STEP_UNGROUNDED = "executable_step_ungrounded"
SECRET_DETECTED = "secret_detected"
VOLATILE_CONTENT = "volatile_content"
INJECTION_ONLY_EVIDENCE = "injection_only_evidence"
LOW_CONFIDENCE = "low_confidence"
WRITE_NOT_REQUESTED = "write_not_requested"
GLOBAL_DISABLED = "global_disabled"
PROJECT_SPECIFIC = "project_specific"
ACCEPTED = "accepted"

MAX_TITLE_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 600
MAX_ENTRY_LENGTH = 1_000

_BROAD_TRIGGER_WORDS = frozenset(
    {
        "bug",
        "code",
        "issue",
        "project",
        "task",
        "work",
        "代码",
        "任务",
        "工作",
        "项目",
        "问题",
        "缺陷",
    }
)
_BROAD_CHINESE_TRIGGER_TERMS = ("代码", "任务", "工作", "项目", "问题", "缺陷")
_FIX_CLAIM_VERBS = frozenset(
    {
        "add",
        "apply",
        "change",
        "configure",
        "create",
        "delete",
        "disable",
        "edit",
        "enable",
        "fix",
        "increase",
        "install",
        "modify",
        "patch",
        "remove",
        "rename",
        "replace",
        "set",
        "uninstall",
        "update",
        "write",
    }
)
_WORKSPACE_MUTATION_TOOLS = frozenset({"write", "edit", "apply_patch", "delete"})
_DETERMINISTIC_STATE_PROOF_TOOLS = frozenset(
    {"git_diff", "git_status", "view", "grep", "read_multi"}
)

_PEM_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_TRUNCATED_PEM_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_ASSIGNED_SECRET_PATTERN = re.compile(
    r"(?ix)"
    r"(?P<prefix>"
    r"[\"']?"
    r"(?=[A-Z0-9_.-]*(?:API[_-]?KEY|TOKEN|PASSWORD|PASSWD|SECRET|COOKIE)"
    r"[A-Z0-9_.-]*[\"']?\s*[:=])"
    r"[A-Z_][A-Z0-9_.-]*[\"']?\s*[:=]\s*"
    r")"
    r"(?:"
    r"(?P<quote>[\"'])(?P<quoted_value>[^\r\n]*?)(?P=quote)"
    r"|(?P<value>[^\s,;\"']+)"
    r")",
)
_AUTHORIZATION_BEARER_PATTERN = re.compile(
    r"(?ix)(?:[\"']authorization[\"']|\bauthorization)\s*:\s*"
    r"(?:[\"']bearer\s+[^\"'\r\n]+[\"']|bearer\s+[^\s,;]+)",
)
_BEARER_PATTERN = re.compile(
    r"(?i)\bbearer\s+(?P<token>[A-Za-z0-9._~+/=-]{20,})",
)
_BEARER_PROSE_ALLOWLIST = frozenset(
    {"authentication", "authorization", "credential", "credentials", "scheme"}
)
_PROVIDER_KEY_PATTERN = re.compile(
    r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b",
)
_ENTROPY_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9+/=_-])")
_CREDENTIAL_CONTEXT_PATTERN = re.compile(
    r"(?i)(?:api[ _-]?key|credential|authorization|auth token|password|passwd|secret|cookie|token)"
)

_TEMP_PATH_PATTERN = re.compile(
    r"(?ix)"
    r"(?:"
    r"[A-Z]:\\Users\\[^\\\s]+\\AppData\\Local\\Temp(?:\\[^\s`]+)?"
    r"|[A-Z]:\\(?:Windows\\)?Temp(?:\\[^\s`]+)?"
    r"|%(?:TEMP|TMP)%"
    r"|\$(?:env:)?(?:TEMP|TMP)\b"
    r"|/(?:var/)?tmp(?:/[^\s`]+)?"
    r")"
)
_LOCALHOST_PORT_PATTERN = re.compile(r"(?i)\b(?:localhost|127\.0\.0\.1|\[::1\]):(?P<port>\d{1,5})\b")
_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:T|\s+)\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_RUNTIME_ID_PATTERN = re.compile(
    r"(?ix)\b(?:session|request)[_-]?id\s*[:=]\s*[\"']?"
    r"(?:"
    r"(?=[A-Z0-9_-]{6,}\b)(?=[A-Z0-9_-]*\d)[A-Z0-9_-]+"
    r"|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"
    r")"
)
_PREFIXED_RUNTIME_ID_PATTERN = re.compile(
    r"(?ix)\b(?:sess(?:ion)?|req(?:uest)?)[_-]"
    r"(?:"
    r"(?=[A-Z0-9_-]{8,}\b)(?=[A-Z0-9_-]*\d)[A-Z0-9_-]+"
    r"|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"
    r")\b"
)
_NUMERIC_TIMESTAMP_PATTERN = re.compile(
    r"(?i)\b(?:timestamp|time)\s*[:=]\s*\d{10,13}\b"
)

_INJECTION_PATTERNS = (
    re.compile(r"(?i)\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b"),
    re.compile(
        r"(?i)\b(?:save|store|write|persist)\b[\s\S]{0,80}"
        r"\b(?:long[- ]term\s+)?(?:memory|instructions?|skill)\b"
    ),
    re.compile(
        r"(?:忽略|无视|覆盖).{0,16}(?:之前|先前|系统).{0,8}(?:指令|提示)",
        re.DOTALL,
    ),
    re.compile(r"(?:保存|写入|存入).{0,32}(?:长期记忆|记忆|技能)", re.DOTALL),
)

_INLINE_CODE_PATTERN = re.compile(r"`([^`\r\n]+)`")
_IMPERATIVE_COMMAND_PATTERN = re.compile(
    r"(?i)^\s*(?:[-*]\s*|\d+[.)]\s*)?(?:run|execute|invoke|运行|执行)\s+(.+?)\s*$"
)
_WRAPPED_COMMAND_PATTERN = re.compile(r"(?i)\b(?:run|execute|invoke|use)\s+(.+?)\s*$")
_COMMAND_PURPOSE_PATTERN = re.compile(
    r"(?i)\s+to\s+(?:check|confirm|inspect|validate|verify)\b.*$"
)
_FIX_CLAIM_PATTERN = re.compile(
    r"(?ix)(?:(?:^\s*(?:[-*]\s*|\d+[.)]\s*)?|[,;]\s*|\bthen\s+)(?:"
    + "|".join(sorted(_FIX_CLAIM_VERBS))
    + r")\b|(?:^|[,;]\s*|然后)(?:设置|更改|更新|修改|修复|添加|删除|替换|启用|禁用|安装))"
)
_PROJECT_RELATIVE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:\.\\|\./)[^\s`]+")
_TEXT_TARGET_PATTERN = re.compile(
    r"(?ix)(?<![A-Z0-9_.-])"
    r"(?:"
    r"[A-Z]:[\\/](?:[A-Z0-9_.-]+[\\/])*[A-Z0-9_.-]+"
    r"|(?:[A-Z0-9_.-]+[\\/])+[A-Z0-9_.-]+"
    r"|[A-Z0-9_-]+\.[A-Z0-9_.-]+"
    r")"
)
_DIFF_TARGET_PATTERN = re.compile(r"(?m)^diff --git a/(.+?) b/(.+?)$")
_STATUS_TARGET_PATTERN = re.compile(r"(?m)^..\s+(.+?)$")
_WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


class SkillGate:
    """Apply the write-governance gates in their security-significant order."""

    def evaluate(
        self,
        delta: SkillDelta,
        trace: TaskTrace,
        *,
        project_root: Path,
        configured_scope: EvolutionScope,
        allow_global: bool,
    ) -> GateDecision:
        for gate in (
            self._schema_gate,
            self._evidence_gate,
            self._secret_gate,
            self._volatility_gate,
            self._injection_gate,
        ):
            decision = gate(delta, trace)
            if decision is not None:
                return decision
        return self._scope_gate(
            delta,
            project_root=project_root,
            configured_scope=configured_scope,
            allow_global=allow_global,
        )

    def _schema_gate(self, delta: SkillDelta, trace: TaskTrace) -> GateDecision | None:
        del trace
        if not isinstance(delta, SkillDelta) or not _has_valid_schema(delta):
            return _reject(SCHEMA_INVALID)
        if not delta.should_write:
            return _reject(WRITE_NOT_REQUESTED)
        if delta.confidence == "low":
            return _reject(LOW_CONFIDENCE)
        return None

    def _evidence_gate(self, delta: SkillDelta, trace: TaskTrace) -> GateDecision | None:
        if not delta.evidence_refs:
            return _reject(EVIDENCE_REF_MISSING)
        for ref in delta.evidence_refs:
            if ref.session_id != trace.session_id or ref.segment_id != trace.segment_id:
                return _reject(EVIDENCE_REF_OUTSIDE_SEGMENT)

        referenced = _referenced_evidence(delta.evidence_refs, trace.evidence)
        if referenced is None:
            return _reject(EVIDENCE_REF_MISSING)

        has_injection_support = any(
            item.source in {EvidenceSource.EXTERNAL_CONTENT, EvidenceSource.WORKSPACE_STATE}
            and _contains_injection(item.content)
            for item in referenced
        )
        if has_injection_support:
            return None
        has_solution_claim, is_grounded = _grounded_solution(delta, referenced)
        if has_solution_claim and not is_grounded:
            return _reject(EXECUTABLE_STEP_UNGROUNDED)
        return None

    def _secret_gate(self, delta: SkillDelta, trace: TaskTrace) -> GateDecision | None:
        del trace
        if any(_contains_sensitive_text(text) for text in _delta_text_fields(delta)):
            return _reject(SECRET_DETECTED)
        return None

    def _volatility_gate(self, delta: SkillDelta, trace: TaskTrace) -> GateDecision | None:
        del trace
        if any(_contains_volatile_text(text) for text in _delta_text_fields(delta)):
            return _reject(VOLATILE_CONTENT)
        return None

    def _injection_gate(self, delta: SkillDelta, trace: TaskTrace) -> GateDecision | None:
        referenced = _referenced_evidence(delta.evidence_refs, trace.evidence)
        if referenced is None:
            return _reject(EVIDENCE_REF_MISSING)
        has_injection_support = any(
            item.source in {EvidenceSource.EXTERNAL_CONTENT, EvidenceSource.WORKSPACE_STATE}
            and _contains_injection(item.content)
            for item in referenced
        )
        if not has_injection_support:
            return None
        has_solution_claim, is_grounded = _grounded_solution(delta, referenced)
        if not has_solution_claim or not is_grounded:
            return _reject(INJECTION_ONLY_EVIDENCE)
        return None

    def _scope_gate(
        self,
        delta: SkillDelta,
        *,
        project_root: Path,
        configured_scope: EvolutionScope,
        allow_global: bool,
    ) -> GateDecision:
        scope = _requested_scope(delta.proposed_scope, configured_scope)
        if scope is EvolutionScope.PROJECT:
            return _accept(delta, EvolutionScope.PROJECT)
        if not allow_global:
            return _downgrade(delta, GLOBAL_DISABLED)
        if _contains_project_specific_text(delta, project_root):
            return _downgrade(delta, PROJECT_SPECIFIC)
        return _accept(delta, EvolutionScope.GLOBAL)


def redact_sensitive_text(value: str) -> str:
    """Replace credential-shaped text without returning the matched value."""

    if not isinstance(value, str) or not value:
        return value
    redacted = _PEM_BLOCK_PATTERN.sub("[REDACTED]", value)
    redacted = _TRUNCATED_PEM_PATTERN.sub("[REDACTED]", redacted)
    redacted = _ASSIGNED_SECRET_PATTERN.sub(r"\g<prefix>[REDACTED]", redacted)
    redacted = _AUTHORIZATION_BEARER_PATTERN.sub("[REDACTED]", redacted)
    redacted = _BEARER_PATTERN.sub(_redact_general_bearer, redacted)
    redacted = _PROVIDER_KEY_PATTERN.sub("[REDACTED]", redacted)
    redacted = _redact_contextual_high_entropy(redacted)
    return redacted


def _redact_general_bearer(match: re.Match[str]) -> str:
    token = match.group("token")
    if token.casefold() in _BEARER_PROSE_ALLOWLIST:
        return match.group(0)
    return "[REDACTED]"


def _has_valid_schema(delta: SkillDelta) -> bool:
    if type(delta.should_write) is not bool:
        return False
    if not _valid_required_text(delta.title, MAX_TITLE_LENGTH):
        return False
    if not _valid_required_text(delta.description, MAX_DESCRIPTION_LENGTH):
        return False
    if not isinstance(delta.proposed_scope, EvolutionScope):
        return False
    if not isinstance(delta.confidence, str) or delta.confidence not in {"low", "medium", "high"}:
        return False
    if not _valid_text_tuple(delta.triggers, minimum=2, maximum=8):
        return False
    if any(_is_only_broad_words(trigger) for trigger in delta.triggers):
        return False
    if len({_normalize_trigger(trigger) for trigger in delta.triggers}) < 2:
        return False
    if not _valid_text_tuple(delta.procedure, minimum=2, maximum=10):
        return False
    if not _valid_text_tuple(delta.verification, minimum=0, maximum=None):
        return False
    if not _valid_text_tuple(delta.pitfalls, minimum=0, maximum=None):
        return False
    if not isinstance(delta.evidence_refs, tuple) or not all(
        isinstance(ref, EvidenceRef) for ref in delta.evidence_refs
    ):
        return False
    return True


def _valid_required_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not _has_disallowed_control(value)
    )


def _valid_text_tuple(value: object, *, minimum: int, maximum: int | None) -> bool:
    if not isinstance(value, tuple) or len(value) < minimum:
        return False
    if maximum is not None and len(value) > maximum:
        return False
    return all(_valid_required_text(item, MAX_ENTRY_LENGTH) for item in value)


def _has_disallowed_control(value: str) -> bool:
    return any(character not in "\n\t" and unicodedata.category(character) == "Cc" for character in value)


def _is_only_broad_words(trigger: str) -> bool:
    words = _normalize_trigger(trigger).split()
    if not words:
        return True
    for word in words:
        if word in _BROAD_TRIGGER_WORDS:
            continue
        remainder = word
        for broad_term in _BROAD_CHINESE_TRIGGER_TERMS:
            remainder = remainder.replace(broad_term, "")
        if remainder:
            return False
    return True


def _normalize_trigger(trigger: str) -> str:
    normalized = unicodedata.normalize("NFKC", trigger).casefold()
    return " ".join(_WORD_PATTERN.findall(normalized))


def _referenced_evidence(
    refs: tuple[EvidenceRef, ...], evidence: tuple[EvidenceItem, ...]
) -> tuple[EvidenceItem, ...] | None:
    by_ref = {item.ref: item for item in evidence}
    if any(ref not in by_ref for ref in refs):
        return None
    selected = frozenset(refs)
    return tuple(item for item in evidence if item.ref in selected)


def _evidence_command(item: EvidenceItem) -> str | None:
    command = item.data.get("command")
    return command if isinstance(command, str) and command.strip() else None


def _entry_command_candidates(step: str, *, mutation_step: bool) -> tuple[str, ...]:
    inline_matches = tuple(_INLINE_CODE_PATTERN.finditer(step))
    inline = tuple(match.group(1).strip() for match in inline_matches if match.group(1).strip())
    plain_step = _INLINE_CODE_PATTERN.sub(" ", step)
    wrapper = _wrapper_command_candidates(plain_step)
    if mutation_step:
        contextual_inline = tuple(
            match.group(1).strip()
            for match in inline_matches
            if re.search(
                r"(?i)\b(?:run|execute|invoke|use|运行|执行)\s*$",
                step[max(0, match.start() - 24) : match.start()],
            )
        )
        return tuple(dict.fromkeys((*contextual_inline, *wrapper)))
    bare = plain_step.strip().rstrip(".")
    return tuple(dict.fromkeys((*inline, *wrapper, bare)))


def _wrapper_command_candidates(step: str) -> tuple[str, ...]:
    match = _IMPERATIVE_COMMAND_PATTERN.fullmatch(step)
    if match is None:
        match = _WRAPPED_COMMAND_PATTERN.search(step)
    if match is None:
        return ()
    candidate = _COMMAND_PURPOSE_PATTERN.sub("", match.group(1)).strip().rstrip(".")
    return (candidate,) if candidate and candidate not in {".", "`"} else ()


def _normalize_command(value: str) -> str:
    return " ".join(value.strip().split())


def _grounded_solution(
    delta: SkillDelta,
    referenced: tuple[EvidenceItem, ...],
) -> tuple[bool, bool]:
    independent = tuple(item for item in referenced if not _contains_injection(item.content))
    grounded_commands = {
        _normalize_command(command)
        for item in independent
        if item.source is EvidenceSource.LOCAL_EXECUTION and item.ok is True
        for command in [_evidence_command(item)]
        if command is not None
    }
    entries = (*delta.procedure, *delta.verification)
    return bool(entries), all(
        _entry_is_grounded(entry, independent, grounded_commands) for entry in entries
    )


def _entry_is_grounded(
    entry: str,
    referenced: tuple[EvidenceItem, ...],
    grounded_commands: set[str],
) -> bool:
    mutation_step = _FIX_CLAIM_PATTERN.search(entry) is not None
    command_candidates = _entry_command_candidates(entry, mutation_step=mutation_step)
    if mutation_step:
        commands_grounded = all(
            _normalize_command(candidate) in grounded_commands
            for candidate in command_candidates
        )
        return commands_grounded and _mutation_entry_is_grounded(
            entry,
            referenced,
            command_candidates=command_candidates,
        )
    return any(
        _normalize_command(candidate) in grounded_commands for candidate in command_candidates
    )


def _mutation_entry_is_grounded(
    entry: str,
    referenced: tuple[EvidenceItem, ...],
    *,
    command_candidates: tuple[str, ...],
) -> bool:
    target_text = entry
    for command in command_candidates:
        target_text = target_text.replace(command, " ")
    targets = _text_targets(target_text)
    if not targets:
        return False
    return all(_target_has_mutation_and_later_proof(target, referenced) for target in targets)


def _target_has_mutation_and_later_proof(
    target: str,
    referenced: tuple[EvidenceItem, ...],
) -> bool:
    last_mutation_index = max(
        (
            index
            for index, item in enumerate(referenced)
            if item.source is EvidenceSource.WORKSPACE_STATE
            and item.ok is True
            and item.tool_name in _WORKSPACE_MUTATION_TOOLS
            and target in _evidence_targets(item)
        ),
        default=-1,
    )
    if last_mutation_index < 0:
        return False
    return any(
        item.source is EvidenceSource.WORKSPACE_STATE
        and item.ok is True
        and item.tool_name in _DETERMINISTIC_STATE_PROOF_TOOLS
        and target in _evidence_targets(item)
        for item in referenced[last_mutation_index + 1 :]
    )


def _text_targets(value: str) -> frozenset[str]:
    return frozenset(
        normalized
        for match in _TEXT_TARGET_PATTERN.finditer(value)
        for normalized in [_normalize_target(match.group(0))]
        if normalized is not None
    )


def _evidence_targets(item: EvidenceItem) -> frozenset[str]:
    values: list[str] = []
    path = item.data.get("path")
    if isinstance(path, str) and path != ".":
        values.append(path)
    for key in ("changed_files", "created_files", "deleted_files"):
        raw_paths = item.data.get(key)
        if isinstance(raw_paths, list):
            values.extend(path for path in raw_paths if isinstance(path, str))
    moved_files = item.data.get("moved_files")
    if isinstance(moved_files, list):
        for moved in moved_files:
            if not isinstance(moved, dict):
                continue
            values.extend(
                path
                for key in ("source", "destination")
                for path in [moved.get(key)]
                if isinstance(path, str)
            )
    for key in ("files", "results"):
        records = item.data.get(key)
        if isinstance(records, list):
            values.extend(
                path
                for record in records
                if isinstance(record, dict)
                for path in [record.get("path")]
                if isinstance(path, str)
            )
    if item.tool_name == "git_diff":
        values.extend(
            path
            for match in _DIFF_TARGET_PATTERN.finditer(item.content)
            for path in match.groups()
        )
    if item.tool_name == "git_status":
        for match in _STATUS_TARGET_PATTERN.finditer(item.content):
            status_path = match.group(1)
            values.extend(part.strip() for part in status_path.split(" -> "))
    normalized_values = {
        normalized
        for value in values
        for normalized in [_normalize_target(value)]
        if normalized is not None
    }
    return frozenset((*normalized_values, *_text_targets(item.content)))


def _normalize_target(value: str) -> str | None:
    candidate = value.strip().strip("`\"'.,:;()[]{}<>").replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    normalized = posixpath.normpath(candidate).casefold()
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def _delta_text_fields(delta: SkillDelta) -> Iterable[str]:
    yield delta.title
    yield delta.description
    yield from delta.triggers
    yield from delta.procedure
    yield from delta.verification
    yield from delta.pitfalls


def _contains_sensitive_text(value: str) -> bool:
    return redact_sensitive_text(value) != value


def _redact_contextual_high_entropy(value: str) -> str:
    matches = list(_ENTROPY_TOKEN_PATTERN.finditer(value))
    for match in reversed(matches):
        token = match.group(0)
        context_start = max(0, match.start() - 64)
        context_end = min(len(value), match.end() + 32)
        context = value[context_start:context_end]
        if _CREDENTIAL_CONTEXT_PATTERN.search(context) and _shannon_entropy(token) >= 4.0:
            value = value[: match.start()] + "[REDACTED]" + value[match.end() :]
    return value


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _contains_volatile_text(value: str) -> bool:
    if (
        _TEMP_PATH_PATTERN.search(value)
        or _TIMESTAMP_PATTERN.search(value)
        or _RUNTIME_ID_PATTERN.search(value)
        or _PREFIXED_RUNTIME_ID_PATTERN.search(value)
        or _NUMERIC_TIMESTAMP_PATTERN.search(value)
    ):
        return True
    for match in _LOCALHOST_PORT_PATTERN.finditer(value):
        port = int(match.group("port"))
        if 49_152 <= port <= 65_535:
            return True
    return False


def _contains_injection(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _INJECTION_PATTERNS)


def _requested_scope(
    proposed_scope: EvolutionScope, configured_scope: EvolutionScope
) -> EvolutionScope:
    if configured_scope is EvolutionScope.PROJECT:
        return EvolutionScope.PROJECT
    if configured_scope is EvolutionScope.GLOBAL:
        return EvolutionScope.GLOBAL
    if proposed_scope is EvolutionScope.GLOBAL:
        return EvolutionScope.GLOBAL
    return EvolutionScope.PROJECT


def _contains_project_specific_text(delta: SkillDelta, project_root: Path) -> bool:
    content = "\n".join(_delta_text_fields(delta))
    project_name = project_root.name.strip()
    if project_name and re.search(
        rf"(?i)(?<![A-Z0-9_]){re.escape(project_name)}(?![A-Z0-9_])",
        content,
    ):
        return True
    if _PROJECT_RELATIVE_PATH_PATTERN.search(content):
        return True
    normalized_content = content.replace("\\", "/").casefold()
    normalized_root = project_root.as_posix().rstrip("/").casefold()
    if normalized_root and normalized_root in normalized_content:
        return True
    if any((project_root / Path(target)).exists() for target in _text_targets(content)):
        return True
    package_names = _project_package_names(project_root)
    return any(
        re.search(
            rf"(?i)(?<![A-Z0-9_]){re.escape(name)}(?=[./\\])"
            rf"|\b(?:python|python3|py)\s+-m\s+{re.escape(name)}(?=$|[\s`'\".,;])",
            content,
        )
        is not None
        for name in package_names
    )


def _project_package_names(project_root: Path) -> tuple[str, ...]:
    if not project_root.is_dir():
        return ()
    try:
        return tuple(
            child.name
            for package_root in (project_root, project_root / "src")
            if package_root.is_dir()
            for child in package_root.iterdir()
            if child.is_dir()
            and child.name.isidentifier()
            and not child.name.startswith("_")
            and (child / "__init__.py").is_file()
        )
    except OSError:
        return ()


def _reject(reason_code: str) -> GateDecision:
    return GateDecision(
        status=GateStatus.REJECT,
        scope=None,
        reason_code=reason_code,
        delta=None,
    )


def _accept(delta: SkillDelta, scope: EvolutionScope) -> GateDecision:
    return GateDecision(
        status=GateStatus.ACCEPT,
        scope=scope,
        reason_code=ACCEPTED,
        delta=delta,
    )


def _downgrade(delta: SkillDelta, reason_code: str) -> GateDecision:
    return GateDecision(
        status=GateStatus.DOWNGRADE_TO_PROJECT,
        scope=EvolutionScope.PROJECT,
        reason_code=reason_code,
        delta=delta,
    )

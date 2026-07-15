"""Deterministic governance for model-produced Forge skill deltas."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
import math
from pathlib import Path
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

_BROAD_TRIGGER_WORDS = ("project", "issue", "code", "项目", "问题", "代码")
_EXECUTABLE_NAMES = frozenset(
    {
        "bash",
        "cargo",
        "cmd",
        "cmake",
        "docker",
        "dotnet",
        "git",
        "go",
        "gradle",
        "java",
        "javac",
        "kubectl",
        "make",
        "mvn",
        "npm",
        "npx",
        "pnpm",
        "poetry",
        "powershell",
        "pwsh",
        "py",
        "pytest",
        "python",
        "python3",
        "sh",
        "uv",
        "yarn",
    }
)

_PEM_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PEM_HEADER_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE,
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
_BEARER_PATTERN = re.compile(
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
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
    r"(?ix)\b(?:session|request)[_-]?id\s*[:=]\s*[\"']?[A-Z0-9][A-Z0-9_-]{5,}"
)
_PREFIXED_RUNTIME_ID_PATTERN = re.compile(
    r"(?i)\b(?:sess(?:ion)?|req(?:uest)?)[_-][A-Za-z0-9][A-Za-z0-9_-]{7,}\b"
)
_NUMERIC_TIMESTAMP_PATTERN = re.compile(
    r"(?i)\b(?:timestamp|time)\s*[:=]\s*\d{10,13}\b"
)

_INJECTION_PATTERNS = (
    re.compile(r"(?i)\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?\b"),
    re.compile(r"(?i)\b(?:save|store|write|persist)\b.{0,80}\b(?:long[- ]term\s+)?(?:memory|instructions?|skill)\b"),
    re.compile(r"(?:忽略|无视|覆盖).{0,16}(?:之前|先前|系统).{0,8}(?:指令|提示)"),
    re.compile(r"(?:保存|写入|存入).{0,32}(?:长期记忆|记忆|技能)"),
)

_INLINE_CODE_PATTERN = re.compile(r"`([^`\r\n]+)`")
_IMPERATIVE_COMMAND_PATTERN = re.compile(
    r"(?i)^\s*(?:[-*]\s*|\d+[.)]\s*)?(?:run|execute|invoke|运行|执行)\s+(.+?)\s*$"
)
_PROJECT_RELATIVE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:\.\\|\./)[^\s`]+")


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

        grounded_commands = {
            _normalize_command(command)
            for item in referenced
            if item.source is EvidenceSource.LOCAL_EXECUTION and item.ok is True
            for command in [_evidence_command(item)]
            if command is not None
        }
        if any(
            _normalize_command(command) not in grounded_commands
            for step in delta.procedure
            for command in _executable_commands(step)
        ):
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
        has_independent_execution = any(
            item.source is EvidenceSource.LOCAL_EXECUTION
            and item.ok is True
            and _evidence_command(item) is not None
            and not _contains_injection(item.content)
            for item in referenced
        )
        if not has_independent_execution:
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
    redacted = _PEM_HEADER_PATTERN.sub("[REDACTED]", redacted)
    redacted = _ASSIGNED_SECRET_PATTERN.sub(r"\g<prefix>[REDACTED]", redacted)
    redacted = _BEARER_PATTERN.sub("[REDACTED]", redacted)
    redacted = _PROVIDER_KEY_PATTERN.sub("[REDACTED]", redacted)
    redacted = _redact_contextual_high_entropy(redacted)
    return redacted


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
    remainder = trigger.casefold()
    for word in _BROAD_TRIGGER_WORDS:
        remainder = remainder.replace(word, "")
    return re.sub(r"[\W_]+", "", remainder, flags=re.UNICODE) == ""


def _referenced_evidence(
    refs: tuple[EvidenceRef, ...], evidence: tuple[EvidenceItem, ...]
) -> tuple[EvidenceItem, ...] | None:
    by_ref = {item.ref: item for item in evidence}
    try:
        return tuple(by_ref[ref] for ref in refs)
    except KeyError:
        return None


def _evidence_command(item: EvidenceItem) -> str | None:
    command = item.data.get("command")
    return command if isinstance(command, str) and command.strip() else None


def _executable_commands(step: str) -> tuple[str, ...]:
    inline = tuple(match.group(1).strip() for match in _INLINE_CODE_PATTERN.finditer(step))
    if inline:
        return tuple(command for command in inline if _looks_like_command(command))
    match = _IMPERATIVE_COMMAND_PATTERN.fullmatch(step)
    if match is None:
        return ()
    candidate = match.group(1).strip().rstrip(".")
    return (candidate,) if _looks_like_command(candidate) else ()


def _looks_like_command(value: str) -> bool:
    candidate = value.lstrip("&").strip()
    if candidate.startswith(("./", ".\\")):
        return True
    first = re.split(r"\s+", candidate, maxsplit=1)[0].casefold()
    executable = Path(first.strip("\"'")).name.casefold()
    return executable in _EXECUTABLE_NAMES or executable.endswith((".exe", ".cmd", ".bat", ".ps1"))


def _normalize_command(value: str) -> str:
    return " ".join(value.strip().split())


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
    package_names = _project_package_names(project_root)
    return any(
        re.search(rf"(?i)(?<![A-Z0-9_]){re.escape(name)}\._[A-Z0-9_]+", content)
        is not None
        for name in package_names
    )


def _project_package_names(project_root: Path) -> tuple[str, ...]:
    if not project_root.is_dir():
        return ()
    try:
        return tuple(
            child.name
            for child in project_root.iterdir()
            if child.is_dir() and child.name.isidentifier() and not child.name.startswith("_")
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

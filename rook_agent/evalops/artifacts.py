"""Redacted, atomic artifact persistence for EvalOps runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from rook_agent.evolution.gate import redact_sensitive_text


_KEY_PARTS = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_SENSITIVE_KEY_PARTS = frozenset(
    {"authorization", "token", "password", "passwd", "secret", "cookie", "apikey"}
)


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Content-addressed reference to one persisted artifact."""

    relative_path: str
    sha256: str
    size_bytes: int


class ArtifactStore:
    """Persist redacted text and JSON beneath a single trusted root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def write_json(self, relative_path: str | Path, value: object) -> ArtifactRef:
        """Redact and atomically persist one deterministic JSON document."""

        redacted = redact_value(value)
        content = _encode_json(redacted) + b"\n"
        return self._write(relative_path, content)

    def write_jsonl(
        self, relative_path: str | Path, values: object
    ) -> ArtifactRef:
        """Redact and atomically persist JSON lines without reordering records."""

        try:
            records = iter(values)  # type: ignore[arg-type]
        except TypeError as exc:
            raise TypeError("JSONL values must be iterable") from exc
        lines = [_encode_json(redact_value(value)) for value in records]
        content = b"\n".join(lines)
        if lines:
            content += b"\n"
        return self._write(relative_path, content)

    def write_text(self, relative_path: str | Path, value: str) -> ArtifactRef:
        """Redact and atomically persist UTF-8 text."""

        if not isinstance(value, str):
            raise TypeError("text artifacts require a string value")
        return self._write(relative_path, redact_sensitive_text(value).encode("utf-8"))

    def _write(self, relative_path: str | Path, content: bytes) -> ArtifactRef:
        target, normalized_path = self._resolve_target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        descriptor_open = True
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                descriptor_open = False
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if descriptor_open:
                os.close(file_descriptor)
            if temporary.exists():
                temporary.unlink()

        return ArtifactRef(
            relative_path=normalized_path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def _resolve_target(self, relative_path: str | Path) -> tuple[Path, str]:
        requested = Path(relative_path)
        if requested.is_absolute():
            raise ValueError("artifact path must be relative")
        target = (self.root / requested).resolve()
        if target == self.root or self.root not in target.parents:
            raise ValueError("artifact path escapes the artifact root")
        return target, target.relative_to(self.root).as_posix()


def redact_value(value: object) -> object:
    """Recursively redact JSON-compatible values before serialization."""

    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list | tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for raw_key, item in value.items():
            key = redact_sensitive_text(str(raw_key))
            redacted[key] = "[REDACTED]" if _is_sensitive_key(key) else redact_value(item)
        return redacted
    return value


def _is_sensitive_key(key: str) -> bool:
    separated = _ACRONYM_BOUNDARY.sub("_", key)
    separated = _CAMEL_BOUNDARY.sub("_", separated).casefold()
    parts = tuple(part for part in _KEY_PARTS.split(separated) if part)
    if any(part in _SENSITIVE_KEY_PARTS for part in parts):
        return True
    return any(left == "api" and right == "key" for left, right in zip(parts, parts[1:]))


def _encode_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = ["ArtifactRef", "ArtifactStore", "redact_value"]

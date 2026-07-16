"""Strict loader for manually authored Skill bundle specifications."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import tomllib

from rook_agent.evalops.models import SkillBundle


_BUNDLE_FIELDS = {
    "name",
    "description",
    "triggers",
    "procedure",
    "verification",
    "pitfalls",
}
_MAX_BUNDLE_BYTES = 64 * 1024
_MAX_DESCRIPTION_CHARS = 2000
_MAX_ITEM_CHARS = 2000
_MAX_ITEMS = 32


def load_skill_bundle(path: str | Path) -> SkillBundle:
    """Load one bounded, evidence-free manual bundle from strict TOML."""

    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError(f"Skill bundle does not exist or is not a file: {source}")
    payload = source.read_bytes()
    if len(payload) > _MAX_BUNDLE_BYTES:
        raise ValueError("Skill bundle exceeds the 64 KiB limit")
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Skill bundle must be valid UTF-8 TOML") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Skill bundle root must be a TOML table")
    unknown = sorted(set(raw) - _BUNDLE_FIELDS)
    if unknown:
        raise ValueError(f"Skill bundle contains unknown fields: {', '.join(unknown)}")

    return SkillBundle(
        name=_required_text(raw, "name", maximum=128),
        description=_required_text(raw, "description", maximum=_MAX_DESCRIPTION_CHARS),
        triggers=_required_items(raw, "triggers"),
        procedure=_required_items(raw, "procedure"),
        verification=_required_items(raw, "verification"),
        pitfalls=_optional_items(raw, "pitfalls"),
        evidence_refs=(),
    )


def _required_text(raw: Mapping[str, object], key: str, *, maximum: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill bundle field {key!r} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"Skill bundle field {key!r} must not have surrounding whitespace")
    if len(value) > maximum:
        raise ValueError(f"Skill bundle field {key!r} exceeds {maximum} characters")
    return value


def _required_items(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    items = _items(raw, key, default=None)
    if not items:
        raise ValueError(f"Skill bundle field {key!r} must contain at least one item")
    return items


def _optional_items(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    return _items(raw, key, default=[])


def _items(
    raw: Mapping[str, object],
    key: str,
    *,
    default: list[object] | None,
) -> tuple[str, ...]:
    value = raw.get(key, default)
    if not isinstance(value, list):
        raise ValueError(f"Skill bundle field {key!r} must be a string list")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"Skill bundle field {key!r} exceeds {_MAX_ITEMS} items")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Skill bundle field {key!r} must contain non-empty strings")
        if item != item.strip():
            raise ValueError(f"Skill bundle field {key!r} items must not have surrounding whitespace")
        if len(item) > _MAX_ITEM_CHARS:
            raise ValueError(f"Skill bundle field {key!r} item exceeds {_MAX_ITEM_CHARS} characters")
        items.append(item)
    if len(set(items)) != len(items):
        raise ValueError(f"Skill bundle field {key!r} must not contain duplicates")
    return tuple(items)


__all__ = ["load_skill_bundle"]

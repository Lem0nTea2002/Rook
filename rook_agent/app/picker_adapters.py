"""Command-specific adapters for the reusable TUI picker."""

from __future__ import annotations

from rook_agent.app.picker import TuiPickerItem, TuiPickerState


def session_picker_item(item: dict[str, object]) -> TuiPickerItem:
    session_id = str(item.get("session_id") or "")
    title = str(item.get("title") or "")
    message_count = item.get("message_count")
    return TuiPickerItem(
        id=session_id,
        label=f"{session_id} {title}".strip(),
        detail=f"messages={message_count}",
    )


def model_picker_item(item: dict[str, object]) -> TuiPickerItem:
    provider = str(item.get("provider") or "")
    model = str(item.get("model") or "")
    spec = f"{provider}/{model}" if provider else model
    return TuiPickerItem(id=spec, label=spec)


def skill_picker_item(item: dict[str, object]) -> TuiPickerItem:
    name = str(item.get("name") or "")
    path = str(item.get("path") or "")
    scope = str(item.get("scope") or "")
    description = str(item.get("description") or "")
    return TuiPickerItem(
        id=path,
        label=name or path,
        detail=description,
        meta={"scope": scope, "path": path},
    )


def permission_mode_picker_item(item: dict[str, object]) -> TuiPickerItem:
    return TuiPickerItem(
        id=str(item.get("id") or ""),
        label=str(item.get("label") or ""),
        detail=str(item.get("description") or ""),
        meta={
            "confirmation_token": str(item.get("confirmation_token") or ""),
        },
    )


def review_permission_picker_item(item: dict[str, object]) -> TuiPickerItem:
    return TuiPickerItem(
        id=str(item.get("id") or ""),
        label=str(item.get("label") or ""),
        detail=str(item.get("description") or ""),
        meta={"confirmation_token": str(item.get("confirmation_token") or "")},
    )


def picker_command(kind: str, item: TuiPickerItem) -> str | None:
    if kind == "resume":
        return f"/resume {item.id}" if item.id else None
    if kind == "model":
        return f"/model {item.id}" if item.id else None
    if kind == "skill":
        return f"/skill-use {item.id}" if item.id else None
    if kind == "permission-mode":
        token = str((item.meta or {}).get("confirmation_token") or "")
        suffix = f" --confirm={token}" if item.id == "full" and token else ""
        return f"/mode {item.id}{suffix}" if item.id else None
    if kind == "review-network":
        token = str((item.meta or {}).get("confirmation_token") or "")
        return f"/review-authorize {item.id} {token}" if item.id and token else None
    return None


def render_picker_item(picker: TuiPickerState, item: TuiPickerItem, index: int) -> str:
    if picker.kind != "skill":
        return f"{item.label} {item.detail}".strip()
    meta = item.meta or {}
    path = meta.get("path") or item.id
    scope = meta.get("scope") or "-"
    lines = [item.label]
    lines.append(f"    {scope} · {path}")
    return "\n".join(lines)


def _truncate_detail(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."

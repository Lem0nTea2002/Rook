"""Project-scoped prompt history used by Ctrl+R."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PromptHistoryEntry:
    text: str
    created_at: str


class PromptHistoryStore:
    """Persist chat prompts only; direct Shell and slash input are excluded."""

    def __init__(self, project_root: str | Path, *, max_entries: int = 500) -> None:
        self.path = Path(project_root).resolve() / ".rook" / "prompt-history.jsonl"
        self.max_entries = max_entries

    def append(self, text: str) -> None:
        normalized = text.strip()
        if not normalized or normalized.startswith(("!", "/")):
            return
        entries = self.load()
        if entries and entries[-1].text == normalized:
            return
        entries.append(
            PromptHistoryEntry(
                text=normalized,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        entries = entries[-self.max_entries :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "".join(
                json.dumps(
                    {"text": entry.text, "created_at": entry.created_at},
                    ensure_ascii=False,
                )
                + "\n"
                for entry in entries
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load(self) -> list[PromptHistoryEntry]:
        if not self.path.is_file():
            return []
        entries: list[PromptHistoryEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                text = str(value["text"]).strip()
                created_at = str(value.get("created_at") or "")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if text:
                entries.append(PromptHistoryEntry(text=text, created_at=created_at))
        return entries[-self.max_entries :]

    def search(self, query: str, *, limit: int = 20) -> tuple[PromptHistoryEntry, ...]:
        normalized = query.strip().casefold()
        results = [
            entry
            for entry in reversed(self.load())
            if not normalized or normalized in entry.text.casefold()
        ]
        return tuple(results[: max(0, limit)])

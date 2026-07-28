"""Safe project file discovery and bounded ``@path`` prompt attachments."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from hashlib import sha256
from html import escape
from pathlib import Path, PureWindowsPath
import re
import subprocess


DEFAULT_MAX_FILE_BYTES = 64 * 1024
DEFAULT_MAX_TOTAL_BYTES = 256 * 1024
_REFERENCE = re.compile(r"(?<!\S)@([^\s]+)")
_SKIPPED_DIRECTORIES = {".git", ".rook", ".venv", "__pycache__", "node_modules"}


@dataclass(frozen=True, slots=True)
class FileReferenceSuggestion:
    path: str
    is_directory: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedFileReference:
    path: str
    sha256: str
    size_bytes: int
    content: str | None


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    original_prompt: str
    enriched_prompt: str
    references: tuple[ResolvedFileReference, ...]
    warnings: tuple[str, ...]


FileLister = Callable[[Path], Iterable[str]]


class ProjectFileReferenceService:
    def __init__(
        self,
        project_root: str | Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        file_lister: FileLister | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.file_lister = file_lister or _list_project_files

    def suggest(self, query: str, *, limit: int = 10) -> tuple[FileReferenceSuggestion, ...]:
        candidates = self._candidates()
        normalized = query.strip().removeprefix("@").casefold()
        ranked: list[tuple[int, str, FileReferenceSuggestion]] = []
        for suggestion in candidates:
            path = suggestion.path.casefold()
            name = Path(suggestion.path).name.casefold()
            score = _reference_score(path, name, normalized, suggestion.is_directory)
            if score is not None:
                ranked.append((score, suggestion.path, suggestion))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in ranked[: max(0, limit)])

    def resolve_prompt(self, prompt: str) -> ResolvedPrompt:
        references: list[ResolvedFileReference] = []
        warnings: list[str] = []
        seen: set[str] = set()
        included_bytes = 0
        for match in _REFERENCE.finditer(prompt):
            raw_path = match.group(1).rstrip(".,;，。；")
            if raw_path in seen:
                continue
            seen.add(raw_path)
            path, relative, error = self._safe_path(raw_path)
            if error:
                warnings.append(error)
                continue
            if path is None or relative is None:
                continue
            if not path.exists():
                warnings.append(f"引用不存在：{relative}")
                continue
            if path.is_dir():
                for child in self._directory_files(relative):
                    if child in seen:
                        continue
                    seen.add(child)
                    resolved, consumed, warning = self._resolve_file(child, included_bytes)
                    if resolved is not None:
                        references.append(resolved)
                        included_bytes += consumed
                    if warning:
                        warnings.append(warning)
                continue
            resolved, consumed, warning = self._resolve_file(relative, included_bytes)
            if resolved is not None:
                references.append(resolved)
                included_bytes += consumed
            if warning:
                warnings.append(warning)

        enriched = _enriched_prompt(prompt, references, warnings)
        return ResolvedPrompt(
            original_prompt=prompt,
            enriched_prompt=enriched,
            references=tuple(references),
            warnings=tuple(warnings),
        )

    def _candidates(self) -> tuple[FileReferenceSuggestion, ...]:
        ignore_patterns = (
            *_ignore_file_patterns(self.project_root / ".gitignore"),
            *_ignore_file_patterns(self.project_root / ".rookignore"),
        )
        files: set[str] = set()
        directories: set[str] = set()
        for raw in self.file_lister(self.project_root):
            relative = _normalized_relative(raw)
            if not relative or _ignored(relative, ignore_patterns):
                continue
            files.add(relative)
            parent = Path(relative).parent
            while str(parent) not in {"", "."}:
                parent_text = parent.as_posix()
                if not _ignored(parent_text, ignore_patterns):
                    directories.add(parent_text)
                parent = parent.parent
        suggestions = [
            *(FileReferenceSuggestion(path, is_directory=True) for path in directories),
            *(FileReferenceSuggestion(path) for path in files),
        ]
        suggestions.sort(key=lambda item: (item.path.casefold(), not item.is_directory))
        return tuple(suggestions)

    def _safe_path(self, raw_path: str) -> tuple[Path | None, str | None, str | None]:
        candidate = Path(raw_path)
        if candidate.is_absolute() or PureWindowsPath(raw_path).is_absolute():
            return None, None, f"拒绝绝对路径引用：{raw_path}"
        if ".." in candidate.parts:
            return None, None, f"拒绝路径遍历引用：{raw_path}"
        unresolved = self.project_root / candidate
        try:
            resolved = unresolved.resolve(strict=False)
            relative = resolved.relative_to(self.project_root).as_posix()
        except ValueError:
            if unresolved.is_symlink():
                return None, None, f"拒绝符号链接逃逸：{raw_path}"
            return None, None, f"拒绝项目外路径引用：{raw_path}"
        return resolved, relative, None

    def _resolve_file(
        self,
        relative: str,
        included_bytes: int,
    ) -> tuple[ResolvedFileReference | None, int, str | None]:
        path, normalized, error = self._safe_path(relative)
        if error or path is None or normalized is None:
            return None, 0, error
        if not path.is_file():
            return None, 0, f"引用不是普通文件：{normalized}"
        size = path.stat().st_size
        digest = _sha256_file(path)
        if size > self.max_file_bytes:
            return (
                ResolvedFileReference(normalized, digest, size, None),
                0,
                f"{normalized} 超过 {self.max_file_bytes} bytes，仅附加路径和哈希",
            )
        data = path.read_bytes()
        if b"\x00" in data:
            return (
                ResolvedFileReference(normalized, digest, size, None),
                0,
                f"{normalized} 是二进制文件，仅附加路径和哈希",
            )
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return (
                ResolvedFileReference(normalized, digest, size, None),
                0,
                f"{normalized} 不是 UTF-8 文本，仅附加路径和哈希",
            )
        if included_bytes + len(data) > self.max_total_bytes:
            return (
                ResolvedFileReference(normalized, digest, size, None),
                0,
                f"{normalized} 超出单次引用总预算 {self.max_total_bytes} bytes，仅附加路径和哈希",
            )
        return ResolvedFileReference(normalized, digest, size, content), len(data), None

    def _directory_files(self, relative_directory: str) -> tuple[str, ...]:
        prefix = f"{relative_directory.rstrip('/')}/"
        return tuple(
            suggestion.path
            for suggestion in self._candidates()
            if not suggestion.is_directory and suggestion.path.startswith(prefix)
        )


def _reference_score(path: str, name: str, query: str, is_directory: bool) -> int | None:
    if not query:
        return 10 + (1 if is_directory else 0)
    if path == query:
        return 100 + (1 if is_directory else 0)
    if name == query:
        return 95
    if path.startswith(query):
        return 90 + (1 if is_directory else 0)
    if name.startswith(query):
        return 80
    if query in path:
        return 70
    return None


def _normalized_relative(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lstrip("./")
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        return ""
    if any(part in _SKIPPED_DIRECTORIES for part in normalized.split("/")):
        return ""
    return normalized


def _ignore_file_patterns(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            patterns.append(value)
    return tuple(patterns)


def _ignored(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch(path, pattern)
        or (pattern.endswith("/") and path.startswith(pattern))
        for pattern in patterns
    )


def _list_project_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return tuple(line for line in result.stdout.splitlines() if line.strip())
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _SKIPPED_DIRECTORIES for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return tuple(files)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _enriched_prompt(
    prompt: str,
    references: list[ResolvedFileReference],
    warnings: list[str],
) -> str:
    if not references and not warnings:
        return prompt
    lines = [prompt, "", "<rook-file-references>"]
    for reference in references:
        attributes = (
            f'path="{escape(reference.path, quote=True)}" '
            f'sha256="{reference.sha256}" size="{reference.size_bytes}"'
        )
        if reference.content is None:
            lines.append(f"  <rook-file {attributes} content=\"path-only\" />")
        else:
            lines.extend(
                [
                    f"  <rook-file {attributes}>",
                    escape(reference.content),
                    "  </rook-file>",
                ]
            )
    for warning in warnings:
        lines.append(f"  <warning>{escape(warning)}</warning>")
    lines.append("</rook-file-references>")
    return "\n".join(lines)

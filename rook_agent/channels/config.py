"""Non-secret global configuration for mobile channels."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
import tomllib

from rook_agent.channels.models import ProjectBinding


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    default_project: str | None = None
    projects: dict[str, ProjectBinding] = field(default_factory=dict)

    def __post_init__(self) -> None:
        projects = dict(self.projects)
        if self.default_project is not None and self.default_project not in projects:
            raise ValueError("default_project must name a configured project")
        for alias, binding in projects.items():
            if alias != binding.alias:
                raise ValueError("project map key must match binding alias")
        object.__setattr__(self, "projects", projects)


def default_channel_config_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return root / "rook" / "channels.toml"
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "rook" / "channels.toml"


def load_channel_config(path: str | Path | None = None) -> ChannelConfig:
    target = Path(path) if path is not None else default_channel_config_path()
    if not target.exists():
        return ChannelConfig()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid channel config: {exc}") from exc
    allowed = {"schema_version", "default_project", "projects"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unsupported channel config keys: {', '.join(sorted(unknown))}")
    if raw.get("schema_version") != 1:
        raise ValueError("channel config schema_version must be 1")
    raw_projects = raw.get("projects", {})
    if not isinstance(raw_projects, dict):
        raise ValueError("projects must be a TOML table")
    projects: dict[str, ProjectBinding] = {}
    for alias, value in raw_projects.items():
        if not isinstance(value, dict) or set(value) != {"path"}:
            raise ValueError(f"project {alias} must contain only path")
        path_value = value.get("path")
        if not isinstance(path_value, str) or not Path(path_value).is_absolute():
            raise ValueError(f"project {alias} path must be absolute")
        projects[alias] = ProjectBinding(alias=alias, path=Path(path_value))
    default_project = raw.get("default_project")
    if default_project is not None and not isinstance(default_project, str):
        raise ValueError("default_project must be a string")
    return ChannelConfig(default_project=default_project, projects=projects)


def save_channel_config(path: str | Path, config: ChannelConfig) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version = 1"]
    if config.default_project is not None:
        lines.append(f"default_project = {_toml_string(config.default_project)}")
    for alias in sorted(config.projects):
        binding = config.projects[alias]
        lines.extend(
            [
                "",
                f"[projects.{_toml_key(alias)}]",
                f"path = {_toml_string(str(binding.path))}",
            ]
        )
    content = "\n".join(lines) + "\n"
    handle, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_key(value: str) -> str:
    if value.replace("-", "").replace("_", "").isalnum():
        return value
    return _toml_string(value)


__all__ = [
    "ChannelConfig",
    "default_channel_config_path",
    "load_channel_config",
    "save_channel_config",
]

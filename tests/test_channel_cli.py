from __future__ import annotations

import json
from pathlib import Path

import pytest

from rook_agent.channels.autostart import WindowsAutostart
from rook_agent.channels.cli import ChannelPaths, run_channel_command
from rook_agent.channels.config import load_channel_config
from rook_agent.channels.state import ChannelStateStore
from rook_agent.cli import build_parser, main


def paths(tmp_path: Path) -> ChannelPaths:
    return ChannelPaths(
        config=tmp_path / "channels.toml",
        state=tmp_path / "channels.sqlite3",
        queue=tmp_path / "queue.sqlite3",
        log=tmp_path / "channel.log",
    )


def test_main_dispatches_channel_commands() -> None:
    called = []

    result = main(
        ["channel", "status", "--json"],
        channel_runner=lambda args: called.append(args.channel_command) or 0,
    )

    assert result == 0
    assert called == ["status"]


def test_project_add_requires_absolute_existing_directory(tmp_path: Path) -> None:
    project = (tmp_path / "repo").resolve()
    project.mkdir()
    selected = paths(tmp_path)
    args = build_parser().parse_args(
        ["channel", "project", "add", "demo", "--path", str(project)]
    )

    assert run_channel_command(args, paths=selected) == 0
    config = load_channel_config(selected.config)
    assert config.projects["demo"].path == project

    bad = build_parser().parse_args(
        ["channel", "project", "add", "bad", "--path", "relative"]
    )
    with pytest.raises(ValueError, match="absolute"):
        run_channel_command(bad, paths=selected)


def test_pair_create_persists_hashed_single_use_code(
    tmp_path: Path,
    capsys,
) -> None:
    project = (tmp_path / "repo").resolve()
    project.mkdir()
    selected = paths(tmp_path)
    run_channel_command(
        build_parser().parse_args(
            ["channel", "project", "add", "demo", "--path", str(project)]
        ),
        paths=selected,
    )

    assert (
        run_channel_command(
            build_parser().parse_args(
                [
                    "channel",
                    "pair",
                    "create",
                    "--channel",
                    "feishu",
                    "--project",
                    "demo",
                ]
            ),
            paths=selected,
        )
        == 0
    )
    output = capsys.readouterr().out
    code = output.split("Pair code: ", 1)[1].splitlines()[0]
    assert len(code) == 6
    assert code.encode() not in selected.state.read_bytes()
    state = ChannelStateStore(selected.state)
    assert state.binding_for is not None


def test_channel_status_json_never_contains_secrets(tmp_path: Path, capsys) -> None:
    selected = paths(tmp_path)
    result = run_channel_command(
        build_parser().parse_args(["channel", "status", "--json"]),
        paths=selected,
        credential_reader=lambda name: "super-secret",
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["feishu"]["configured"] is True
    assert payload["weixin"]["configured"] is True
    assert "super-secret" not in json.dumps(payload)


def test_windows_autostart_builds_user_task_without_secrets(tmp_path: Path) -> None:
    calls = []
    autostart = WindowsAutostart(
        executable=Path("C:/Python/python.exe"),
        runner=lambda command: calls.append(command) or 0,
    )

    autostart.install(("feishu", "weixin"))

    command = calls[0]
    rendered = " ".join(command)
    assert command[0].lower().endswith("schtasks.exe")
    assert "/RL" in command and "LIMITED" in command
    assert "channel serve --channels feishu,weixin" in rendered
    assert "token" not in rendered.lower()
    assert "secret" not in rendered.lower()

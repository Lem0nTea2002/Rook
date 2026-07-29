from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rook_agent.channels.cli as channel_cli
from rook_agent.channels.autostart import WindowsAutostart
from rook_agent.channels.cli import (
    ChannelPaths,
    _LiveSmokeRunner,
    _render_qr_ascii,
    run_channel_command,
)
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


def test_channel_smoke_parser_is_explicit_and_bounded() -> None:
    args = build_parser().parse_args(
        ["channel", "smoke", "--channels", "feishu,weixin"]
    )

    assert args.channel_command == "smoke"
    assert args.channels == "feishu,weixin"


def test_live_smoke_runner_writes_marker_only_after_allow_once(
    tmp_path: Path,
) -> None:
    runner = _LiveSmokeRunner(tmp_path)

    asyncio.run(runner.arun_user_turn("smoke"))
    assert not runner.marker.exists()
    asyncio.run(
        runner.aresume_with_user_input("channel-live-smoke-write", "deny")
    )
    assert not runner.marker.exists()

    asyncio.run(runner.arun_user_turn("smoke"))
    asyncio.run(
        runner.aresume_with_user_input("channel-live-smoke-write", "allow_once")
    )
    assert runner.marker.read_text(encoding="utf-8") == (
        "Rook Mobile Channel Live Smoke\n"
    )


def test_qr_renderer_is_ascii_only_for_windows_terminals() -> None:
    rendered = _render_qr_ascii(
        [
            [False, True, False],
            [True, True, True],
            [False, True, False],
        ]
    )

    assert rendered.splitlines() == ["  ##  ", "######", "  ##  "]
    assert rendered.isascii()


def test_feishu_setup_registers_dedicated_app_without_printing_secret(
    capsys,
) -> None:
    stored: dict[str, str] = {}
    registered: list[bool] = []
    args = build_parser().parse_args(["channel", "setup", "feishu"])

    result = run_channel_command(
        args,
        credential_writer=stored.__setitem__,
        feishu_registrar=lambda: (
            registered.append(True)
            or {
                "client_id": "cli_rook",
                "client_secret": "registered-secret",
            }
        ),
    )

    assert result == 0
    assert registered == [True]
    assert json.loads(stored["channel:feishu:default"]) == {
        "app_id": "cli_rook",
        "app_secret": "registered-secret",
    }
    output = capsys.readouterr().out
    assert "cli_rook" in output
    assert "registered-secret" not in output


def test_feishu_setup_rejects_incomplete_registration_result() -> None:
    args = build_parser().parse_args(["channel", "setup", "feishu"])

    with pytest.raises(ValueError, match="incomplete"):
        run_channel_command(
            args,
            credential_writer=lambda _name, _value: pytest.fail(
                "incomplete credentials must not be stored"
            ),
            feishu_registrar=lambda: {"client_id": "cli_rook"},
        )


def test_feishu_setup_can_reuse_existing_app_with_hidden_secret(
    monkeypatch,
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        channel_cli.getpass,
        "getpass",
        lambda _prompt: "local-secret",
    )

    result = run_channel_command(
        build_parser().parse_args(
            ["channel", "setup", "feishu", "--app-id", "cli_existing"]
        ),
        credential_writer=stored.__setitem__,
        feishu_registrar=lambda: pytest.fail(
            "manual setup must not create another application"
        ),
    )

    assert result == 0
    assert json.loads(stored["channel:feishu:default"]) == {
        "app_id": "cli_existing",
        "app_secret": "local-secret",
    }


def test_feishu_registration_requests_only_mobile_gateway_capabilities(
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}

    def register_app(**kwargs):
        captured.update(kwargs)
        kwargs["on_qr_code"](
            {
                "url": "https://accounts.feishu.cn/device",
                "expire_in": 60,
            }
        )
        return {
            "client_id": "cli_rook",
            "client_secret": "registered-secret",
        }

    monkeypatch.setattr(
        channel_cli.importlib,
        "import_module",
        lambda name: SimpleNamespace(register_app=register_app),
    )
    rendered: list[str] = []
    monkeypatch.setattr(channel_cli, "_print_qr", rendered.append)

    result = channel_cli._register_feishu_application()

    assert result["client_id"] == "cli_rook"
    assert captured["create_only"] is True
    assert captured["addons"] == {
        "scopes": {
            "tenant": [
                "im:message.p2p_msg:readonly",
                "im:message:send_as_bot",
            ],
        },
        "events": {
            "items": {
                "tenant": ["im.message.receive_v1"],
            },
        },
        "callbacks": {
            "items": ["card.action.trigger"],
        },
    }
    assert rendered == ["https://accounts.feishu.cn/device"]
    assert "registered-secret" not in capsys.readouterr().out


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

    def run(command):
        calls.append(command)
        return 1 if "/Query" in command else 0

    autostart = WindowsAutostart(
        executable=Path("C:/Python/python.exe"),
        runner=run,
    )

    autostart.install(("feishu", "weixin"))

    command = calls[1]
    rendered = " ".join(command)
    assert command[0].lower().endswith("schtasks.exe")
    assert "/RL" in command and "LIMITED" in command
    assert "channel serve --channels feishu,weixin" in rendered
    assert "token" not in rendered.lower()
    assert "secret" not in rendered.lower()


def test_windows_autostart_refuses_to_overwrite_existing_task() -> None:
    calls = []
    autostart = WindowsAutostart(
        executable=Path("C:/Python/python.exe"),
        runner=lambda command: calls.append(command) or 0,
    )

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        autostart.install(("feishu",))

    assert len(calls) == 1
    assert "/Query" in calls[0]

"""CLI handlers for the local mobile-channel gateway."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import getpass
import importlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Callable

from rook_agent.app.factory import RookRuntime, create_rook_runtime
from rook_agent.app.runtime import AgentChatRunner
from rook_agent.channels.base import ChannelAdapter
from rook_agent.channels.autostart import create_autostart
from rook_agent.channels.config import (
    ChannelConfig,
    default_channel_config_path,
    load_channel_config,
    save_channel_config,
)
from rook_agent.channels.feishu import FeishuAdapter
from rook_agent.channels.gateway import ChannelGateway
from rook_agent.channels.models import ChannelKind, ProjectBinding
from rook_agent.channels.state import ChannelStateStore
from rook_agent.channels.weixin import (
    WeixinAdapter,
    WeixinCredentials,
    WeixinLoginClient,
)
from rook_agent.config.credentials import read_secret, write_secret


FEISHU_CREDENTIAL = "channel:feishu:default"
WEIXIN_CREDENTIAL = "channel:weixin:default"
FeishuRegistrar = Callable[[], dict[str, object]]


@dataclass(frozen=True, slots=True)
class ChannelPaths:
    config: Path
    state: Path
    queue: Path
    log: Path


def default_channel_paths() -> ChannelPaths:
    config = default_channel_config_path()
    root = config.parent
    return ChannelPaths(
        config=config,
        state=root / "channels.sqlite3",
        queue=root / "channel-queue.sqlite3",
        log=root / "channel.log",
    )


def run_channel_command(
    args: argparse.Namespace,
    *,
    paths: ChannelPaths | None = None,
    credential_reader: Callable[[str], str | None] = read_secret,
    credential_writer: Callable[[str, str], None] = write_secret,
    feishu_registrar: FeishuRegistrar | None = None,
) -> int:
    selected = paths or default_channel_paths()
    command = args.channel_command
    if command == "project":
        return _run_project(args, selected)
    if command == "pair":
        return _run_pair(args, selected)
    if command == "status":
        return _run_status(args, selected, credential_reader)
    if command == "setup":
        return _run_setup(
            args,
            credential_writer,
            feishu_registrar or _register_feishu_application,
        )
    if command == "login":
        return _run_login(args, credential_writer)
    if command == "serve":
        return _run_serve(args, selected, credential_reader)
    if command == "smoke":
        return _run_smoke(args, selected, credential_reader)
    if command == "autostart":
        return _run_autostart(args)
    raise ValueError("a channel subcommand is required")


def _run_project(args: argparse.Namespace, paths: ChannelPaths) -> int:
    if args.project_command != "add":
        raise ValueError("unsupported project command")
    raw_path = Path(args.path)
    if not raw_path.is_absolute():
        raise ValueError("project path must be absolute")
    if raw_path.is_symlink():
        raise ValueError("project path must not be a symbolic link")
    project_path = raw_path.resolve()
    if not project_path.is_dir():
        raise ValueError("project path must be an existing directory")
    config = load_channel_config(paths.config)
    projects = dict(config.projects)
    projects[args.alias] = ProjectBinding(alias=args.alias, path=project_path)
    default_project = config.default_project or args.alias
    save_channel_config(
        paths.config,
        ChannelConfig(default_project=default_project, projects=projects),
    )
    print(f"Project added: {args.alias} -> {project_path}")
    return 0


def _run_pair(args: argparse.Namespace, paths: ChannelPaths) -> int:
    if args.pair_command != "create":
        raise ValueError("unsupported pair command")
    config = load_channel_config(paths.config)
    if args.project not in config.projects:
        raise ValueError("pairing project is not in the local whitelist")
    state = ChannelStateStore(paths.state)
    code = state.create_pair_code(ChannelKind(args.channel), args.project)
    print(f"Pair code: {code}")
    print("Expires in 10 minutes and can be used once.")
    print(f"Send `/pair {code}` in the selected private chat.")
    return 0


def _run_status(
    args: argparse.Namespace,
    paths: ChannelPaths,
    credential_reader: Callable[[str], str | None],
) -> int:
    config = load_channel_config(paths.config)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "config_path": str(paths.config),
        "state_path": str(paths.state),
        "projects": {
            alias: str(binding.path)
            for alias, binding in sorted(config.projects.items())
        },
        "default_project": config.default_project,
        "feishu": {"configured": credential_reader(FEISHU_CREDENTIAL) is not None},
        "weixin": {"configured": credential_reader(WEIXIN_CREDENTIAL) is not None},
        "autostart": {"installed": create_autostart().status()},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Config: {payload['config_path']}")
        print(f"Projects: {', '.join(payload['projects']) or 'none'}")
        print(f"Feishu: {'configured' if payload['feishu']['configured'] else 'not configured'}")
        print(f"WeChat: {'configured' if payload['weixin']['configured'] else 'not configured'}")
        print(f"Autostart: {'installed' if payload['autostart']['installed'] else 'not installed'}")
    return 0


def _run_setup(
    args: argparse.Namespace,
    credential_writer: Callable[[str, str], None],
    feishu_registrar: FeishuRegistrar,
) -> int:
    if args.setup_channel != "feishu":
        raise ValueError("only Feishu setup is supported by this command")
    if args.app_id:
        app_id = str(args.app_id).strip()
        app_secret = getpass.getpass("Feishu App Secret: ").strip()
    else:
        registration = feishu_registrar()
        app_id = _required_registration_value(registration, "client_id")
        app_secret = _required_registration_value(registration, "client_secret")
    if not app_id or not app_secret:
        raise ValueError("Feishu App ID and App Secret are required")
    credential_writer(
        FEISHU_CREDENTIAL,
        json.dumps({"app_id": app_id, "app_secret": app_secret}, separators=(",", ":")),
    )
    print(f"Feishu app configured: {app_id}")
    print("Feishu credentials stored in the operating-system credential manager.")
    return 0


def _register_feishu_application() -> dict[str, object]:
    try:
        lark = importlib.import_module("lark_oapi")
    except ImportError as exc:
        raise RuntimeError(
            'Feishu setup requires channel extras: pip install "rook-agent[im]"'
        ) from exc

    def show_qr(info: object) -> None:
        if not isinstance(info, dict):
            raise ValueError("Feishu registration QR response is invalid")
        raw_url = info.get("url")
        if not isinstance(raw_url, str) or not raw_url:
            raise ValueError("Feishu registration QR response is incomplete")
        print("Scan with Feishu to create a dedicated Rook application:")
        print(raw_url)
        _print_qr(raw_url)
        expire_in = info.get("expire_in")
        if isinstance(expire_in, int):
            print(f"QR expires in {expire_in} seconds.")

    raw_result = lark.register_app(
        on_qr_code=show_qr,
        source="rook-0.4.0",
        app_preset={
            "name": "Rook",
            "desc": "Local coding agent mobile gateway",
        },
        addons={
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
        },
        create_only=True,
    )
    if not isinstance(raw_result, dict):
        raise ValueError("Feishu registration result is invalid")
    result: dict[str, object] = {}
    for key, value in raw_result.items():
        if isinstance(key, str):
            result[key] = value
    return result


def _required_registration_value(
    registration: dict[str, object],
    name: str,
) -> str:
    value = registration.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Feishu registration result is incomplete")
    return value.strip()


def _run_login(
    args: argparse.Namespace,
    credential_writer: Callable[[str, str], None],
) -> int:
    if args.login_channel != "weixin":
        raise ValueError("only WeChat iLink login is supported by this command")
    client = WeixinLoginClient()
    qr = client.get_qrcode()
    print("Scan this official WeChat iLink QR payload with WeChat:")
    _print_qr(qr.image_content)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = client.poll_status(qr.qrcode)
        name = str(status.get("status") or "")
        if name == "confirmed":
            payload = {
                "bot_token": str(status.get("bot_token") or ""),
                "bot_id": str(status.get("ilink_bot_id") or ""),
                "ilink_user_id": str(status.get("ilink_user_id") or ""),
                "base_url": str(status.get("baseurl") or "https://ilinkai.weixin.qq.com"),
            }
            WeixinCredentials(**payload)
            credential_writer(
                WEIXIN_CREDENTIAL,
                json.dumps(payload, separators=(",", ":")),
            )
            print("WeChat iLink login stored in the operating-system credential manager.")
            return 0
        if name in {"expired", "scaned_but_redirect", "binded_redirect", "verify_code_blocked"}:
            raise RuntimeError(f"WeChat QR login stopped with status: {name}")
        time.sleep(1)
    raise RuntimeError("WeChat QR login expired after 5 minutes")


def _run_serve(
    args: argparse.Namespace,
    paths: ChannelPaths,
    credential_reader: Callable[[str], str | None],
) -> int:
    config = load_channel_config(paths.config)
    if not config.projects:
        raise ValueError("add at least one project before serving channels")
    state = ChannelStateStore(paths.state)
    channels = _parse_channels(args.channels)
    adapters = _create_adapters(channels, state, credential_reader)
    _configure_logging(paths.log)
    runtimes: dict[str, RookRuntime] = {}

    def runtime_factory(binding: ProjectBinding, session_id: str) -> AgentChatRunner:
        runtime = create_rook_runtime(
            project_root=binding.path,
            data_root=binding.path / ".rook",
            session_id=session_id,
            resume_existing=True,
        )
        # Channel progress is ack/typing based. Non-streaming execution keeps
        # the cross-process project lock off the asyncio event-loop thread.
        runtime.chat_runner.use_streaming = False
        runtimes[session_id] = runtime
        return runtime.chat_runner

    gateway = ChannelGateway(
        adapters=adapters,
        state=state,
        config=config,
        runtime_factory=runtime_factory,
        queue_path=paths.queue,
        max_concurrency=2,
    )
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        pass
    finally:
        for runtime in runtimes.values():
            runtime.close()
    return 0


def _run_smoke(
    args: argparse.Namespace,
    paths: ChannelPaths,
    credential_reader: Callable[[str], str | None],
) -> int:
    config = load_channel_config(paths.config)
    if not config.projects:
        raise ValueError("add a dedicated test project before running channel smoke")
    state = ChannelStateStore(paths.state)
    channels = _parse_channels(args.channels)
    adapters = _create_adapters(channels, state, credential_reader)
    _configure_logging(paths.log)

    def runtime_factory(binding: ProjectBinding, session_id: str) -> _LiveSmokeRunner:
        return _LiveSmokeRunner(binding.path)

    gateway = ChannelGateway(
        adapters=adapters,
        state=state,
        config=config,
        runtime_factory=runtime_factory,
        queue_path=paths.queue,
        max_concurrency=2,
    )
    print("Rook channel Live Smoke：真实 IM + 本地 Fake Runner，不调用模型。")
    print("请在已配对私聊发送任意任务，批准后再发送 /diff 和 /cancel。")
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        pass
    return 0


def _create_adapters(
    channels: tuple[ChannelKind, ...],
    state: ChannelStateStore,
    credential_reader: Callable[[str], str | None],
) -> dict[ChannelKind, ChannelAdapter]:
    adapters: dict[ChannelKind, ChannelAdapter] = {}
    if ChannelKind.FEISHU in channels:
        raw = _load_credential(credential_reader, FEISHU_CREDENTIAL, "Feishu")
        adapters[ChannelKind.FEISHU] = FeishuAdapter(
            app_id=str(raw["app_id"]),
            app_secret=str(raw["app_secret"]),
        )
    if ChannelKind.WEIXIN in channels:
        raw = _load_credential(credential_reader, WEIXIN_CREDENTIAL, "WeChat")
        credentials = WeixinCredentials(
            bot_token=str(raw["bot_token"]),
            bot_id=str(raw["bot_id"]),
            ilink_user_id=str(raw["ilink_user_id"]),
            base_url=str(raw.get("base_url") or "https://ilinkai.weixin.qq.com"),
        )
        adapters[ChannelKind.WEIXIN] = WeixinAdapter(
            credentials=credentials,
            cursor_loader=lambda: state.load_cursor(ChannelKind.WEIXIN, credentials.bot_id),
            cursor_saver=lambda cursor: state.save_cursor(
                ChannelKind.WEIXIN,
                credentials.bot_id,
                cursor,
            ),
        )
    return adapters


class _LiveSmokeRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.marker = project_root / "rook-mobile-smoke.txt"
        self.last_display_lines: list[str] = []
        self.last_pending_input: object | None = None

    async def arun_user_turn(self, content: str) -> object:
        self.last_display_lines = []
        self.last_pending_input = SimpleNamespace(
            id="channel-live-smoke-write",
            kind="permission_confirmation",
            payload={
                "tool_name": "smoke_write",
                "permission_request": {
                    "action": "write_path",
                    "target": str(self.marker),
                },
            },
        )
        return SimpleNamespace(content="等待手机端单次审批。")

    async def aresume_with_user_input(self, request_id: str, answer: str) -> object:
        if request_id != "channel-live-smoke-write":
            raise ValueError("Live Smoke approval request does not match")
        self.last_pending_input = None
        if answer == "allow_once":
            self.marker.write_text(
                "Rook Mobile Channel Live Smoke\n",
                encoding="utf-8",
            )
            content = "Live Smoke 已获单次批准并写入 marker；未调用模型。"
        else:
            content = "Live Smoke 已拒绝；未写入文件，也未调用模型。"
        self.last_display_lines = [content]
        return SimpleNamespace(content=content)

    def cancel_current_turn(self) -> None:
        self.last_display_lines = ["Live Smoke 已收到取消请求。"]


def _run_autostart(args: argparse.Namespace) -> int:
    autostart = create_autostart()
    if args.autostart_command == "install":
        autostart.install(tuple(value.value for value in _parse_channels(args.channels)))
        print("Current-user channel autostart installed.")
    elif args.autostart_command == "remove":
        autostart.remove()
        print("Current-user channel autostart removed.")
    elif args.autostart_command == "status":
        print("installed" if autostart.status() else "not installed")
    else:
        raise ValueError("unsupported autostart command")
    return 0


def _parse_channels(value: str) -> tuple[ChannelKind, ...]:
    values = tuple(ChannelKind(item.strip()) for item in value.split(",") if item.strip())
    if not values or len(set(values)) != len(values):
        raise ValueError("channels must contain unique feishu and/or weixin values")
    return values


def _load_credential(
    reader: Callable[[str], str | None],
    name: str,
    label: str,
) -> dict[str, object]:
    raw = reader(name)
    if raw is None:
        raise ValueError(f"{label} is not configured")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} credential record is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} credential record is invalid")
    return value


def _configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("rook_agent.channels")
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _print_qr(content: str) -> None:
    try:
        import qrcode
    except ImportError:
        print(content)
        print('Install channel extras for a terminal QR: pip install "rook-agent[im]"')
        return
    image = qrcode.QRCode(border=1)
    image.add_data(content)
    image.make(fit=True)
    print(_render_qr_ascii(image.get_matrix()))


def _render_qr_ascii(matrix: list[list[bool]]) -> str:
    return "\n".join(
        "".join("##" if cell else "  " for cell in row)
        for row in matrix
    )


__all__ = ["ChannelPaths", "default_channel_paths", "run_channel_command"]

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from rook_agent.channels.base import ChannelAdapter
from rook_agent.channels.config import ChannelConfig, load_channel_config, save_channel_config
from rook_agent.channels.gateway import ChannelGateway
from rook_agent.channels.models import ChannelKind, InboundMessage, ProjectBinding
from rook_agent.channels.state import ChannelStateStore
from rook_agent.execution.models import JobStatus


class FakeAdapter(ChannelAdapter):
    def __init__(self, channel: ChannelKind) -> None:
        self.channel = channel
        self.sent: list[tuple[str, str, str | None]] = []
        self.typing: list[tuple[str, bool]] = []
        self.closed = False

    async def run(self, handler):
        return None

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        context_token: str | None = None,
    ) -> None:
        self.sent.append((conversation_id, text, reply_to))

    async def set_typing(
        self,
        conversation_id: str,
        active: bool,
        *,
        context_token: str | None = None,
    ) -> None:
        self.typing.append((conversation_id, active))

    async def close(self) -> None:
        self.closed = True


class FailingReplyAdapter(FakeAdapter):
    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        context_token: str | None = None,
    ) -> None:
        if text == "done":
            raise ConnectionError("simulated delivery failure")
        await super().send(
            conversation_id,
            text,
            reply_to=reply_to,
            context_token=context_token,
        )


class FakeRunner:
    def __init__(self, reply: str = "done") -> None:
        self.reply = reply
        self.calls: list[str] = []
        self.last_display_lines: list[str] = []
        self.last_pending_input = None
        self.cancelled = False

    async def arun_user_turn(self, content: str):
        self.calls.append(content)
        self.last_display_lines = [self.reply]
        return type("Response", (), {"content": self.reply})()

    async def aresume_with_user_input(self, request_id: str, answer: str):
        self.calls.append(f"{request_id}:{answer}")
        self.last_display_lines = [self.reply]
        return type("Response", (), {"content": self.reply})()

    def cancel_current_turn(self) -> None:
        self.cancelled = True


class PermissionRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__("waiting")
        self.resumed: list[tuple[str, str]] = []

    async def arun_user_turn(self, content: str):
        self.calls.append(content)
        self.last_pending_input = SimpleNamespace(
            id="permission-1",
            kind="permission_confirmation",
            question="Allow write?",
            payload={
                "tool_name": "write",
                "permission_request": {
                    "action": "write_path",
                    "target": "README.md",
                },
            },
        )
        return type("Response", (), {"content": "waiting"})()

    async def aresume_with_user_input(self, request_id: str, answer: str):
        self.resumed.append((request_id, answer))
        self.last_pending_input = None
        self.last_display_lines = ["approved" if answer == "allow_once" else "denied"]
        return type("Response", (), {"content": self.last_display_lines[0]})()


def message(
    *,
    message_id: str = "m1",
    user_id: str = "u1",
    text: str = "fix tests",
    channel: ChannelKind = ChannelKind.FEISHU,
    account_id: str = "acct",
    conversation_id: str = "chat",
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        account_id=account_id,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        text=text,
        received_at=100.0,
        context_token="ctx",
    )


def test_channel_config_round_trip_and_rejects_relative_project(tmp_path: Path) -> None:
    path = tmp_path / "channels.toml"
    project = (tmp_path / "repo").resolve()
    config = ChannelConfig(
        default_project="demo",
        projects={"demo": ProjectBinding(alias="demo", path=project)},
    )

    save_channel_config(path, config)

    assert load_channel_config(path) == config
    path.write_text(
        'schema_version = 1\n[projects.demo]\npath = "../escape"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="absolute"):
        load_channel_config(path)


def test_pair_code_is_single_use_bound_to_channel_and_expires(tmp_path: Path) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: 100.0)
    code = state.create_pair_code(ChannelKind.FEISHU, "demo", ttl_seconds=600, code="ABC123")

    binding = state.consume_pair_code(code, message())

    assert binding.project_alias == "demo"
    assert state.binding_for(message()) == binding
    assert state.consume_pair_code(code, message(message_id="m2")) is None
    state.create_pair_code(ChannelKind.FEISHU, "demo", code="OTHER1")
    assert (
        state.consume_pair_code(
            "OTHER1",
            message(message_id="m3", user_id="another-user"),
        )
        is None
    )
    expired = ChannelStateStore(tmp_path / "other.sqlite3", clock=lambda: 1000.0)
    expired.create_pair_code(ChannelKind.FEISHU, "demo", ttl_seconds=10, code="OLD111")
    expired.clock = lambda: 1011.0
    assert expired.consume_pair_code("OLD111", message()) is None


def test_message_claim_is_idempotent_under_concurrency(tmp_path: Path) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3")

    async def exercise() -> list[bool]:
        async def claim_once() -> bool:
            return await asyncio.to_thread(state.claim_message, message())

        return list(await asyncio.gather(*(claim_once() for _ in range(100))))

    results = asyncio.run(exercise())
    assert sum(results) == 1


def test_five_thousand_duplicate_deliveries_produce_one_claim(tmp_path: Path) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3")

    claims = [state.claim_message(message()) for _ in range(5_000)]

    assert claims.count(True) == 1


def test_gateway_rejects_unpaired_user_then_runs_paired_task_once(tmp_path: Path) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: 100.0)
    adapter = FakeAdapter(ChannelKind.FEISHU)
    runner = FakeRunner()
    project = (tmp_path / "repo").resolve()
    project.mkdir()
    gateway = ChannelGateway(
        adapters={ChannelKind.FEISHU: adapter},
        state=state,
        config=ChannelConfig(
            default_project="demo",
            projects={"demo": ProjectBinding(alias="demo", path=project)},
        ),
        runtime_factory=lambda binding, session_id: runner,
        queue_path=tmp_path / "queue.sqlite3",
    )

    asyncio.run(gateway.accept(message()))
    assert "尚未配对" in adapter.sent[-1][1]

    state.create_pair_code(ChannelKind.FEISHU, "demo", ttl_seconds=600, code="PAIR12")
    asyncio.run(gateway.accept(message(message_id="pair", text="/pair PAIR12")))
    asyncio.run(gateway.accept(message(message_id="task")))
    asyncio.run(gateway.accept(message(message_id="task")))
    asyncio.run(gateway.run_pending_once())

    assert runner.calls == ["fix tests"]
    assert any("已进入队列" in text for _, text, _ in adapter.sent)
    assert adapter.sent[-1][1] == "done"


def test_gateway_does_not_mark_job_succeeded_before_reply_is_delivered(
    tmp_path: Path,
) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: 100.0)
    adapter = FailingReplyAdapter(ChannelKind.FEISHU)
    project = (tmp_path / "repo").resolve()
    project.mkdir()
    gateway = ChannelGateway(
        adapters={ChannelKind.FEISHU: adapter},
        state=state,
        config=ChannelConfig(
            default_project="demo",
            projects={"demo": ProjectBinding(alias="demo", path=project)},
        ),
        runtime_factory=lambda binding, session_id: FakeRunner(),
        queue_path=tmp_path / "queue.sqlite3",
    )
    state.create_pair_code(ChannelKind.FEISHU, "demo", code="PAIR12")
    asyncio.run(gateway.accept(message(message_id="pair", text="/pair PAIR12")))
    asyncio.run(gateway.accept(message(message_id="task")))

    assert asyncio.run(gateway.run_pending_once()) is True

    stats = gateway.queue.stats()
    assert stats[JobStatus.SUCCEEDED] == 0
    assert stats[JobStatus.FAILED] == 1
    job_id = next(text.rsplit("：", 1)[1] for _, text, _ in adapter.sent if "已进入队列" in text)
    assert gateway.queue.get(job_id).last_error == "channel_delivery_failed"
    assert any("任务执行失败" in text for _, text, _ in adapter.sent)


def test_gateway_serializes_same_session_but_allows_two_global_projects(tmp_path: Path) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3")
    config = ChannelConfig(
        projects={
            "one": ProjectBinding(alias="one", path=(tmp_path / "one").resolve()),
            "two": ProjectBinding(alias="two", path=(tmp_path / "two").resolve()),
        }
    )
    for binding in config.projects.values():
        binding.path.mkdir()
    adapter = FakeAdapter(ChannelKind.FEISHU)
    gateway = ChannelGateway(
        adapters={ChannelKind.FEISHU: adapter},
        state=state,
        config=config,
        runtime_factory=lambda binding, session_id: FakeRunner(),
        queue_path=tmp_path / "queue.sqlite3",
        max_concurrency=2,
    )
    assert gateway.max_concurrency == 2


def test_gateway_enforces_one_turn_per_session_and_two_global_slots(
    tmp_path: Path,
) -> None:
    class Counter:
        active = 0
        maximum = 0

    class SlowRunner(FakeRunner):
        async def arun_user_turn(self, content: str):
            Counter.active += 1
            Counter.maximum = max(Counter.maximum, Counter.active)
            await asyncio.sleep(0.03)
            Counter.active -= 1
            self.last_display_lines = [content]
            return type("Response", (), {"content": content})()

    state = ChannelStateStore(tmp_path / "state.sqlite3")
    one = (tmp_path / "one").resolve()
    two = (tmp_path / "two").resolve()
    one.mkdir()
    two.mkdir()
    adapters = {
        ChannelKind.FEISHU: FakeAdapter(ChannelKind.FEISHU),
        ChannelKind.WEIXIN: FakeAdapter(ChannelKind.WEIXIN),
    }
    gateway = ChannelGateway(
        adapters=adapters,
        state=state,
        config=ChannelConfig(
            projects={
                "one": ProjectBinding(alias="one", path=one),
                "two": ProjectBinding(alias="two", path=two),
            }
        ),
        runtime_factory=lambda binding, session_id: SlowRunner(),
        queue_path=tmp_path / "queue.sqlite3",
        max_concurrency=2,
    )
    state.create_pair_code(ChannelKind.FEISHU, "one", code="PAIR11")
    state.create_pair_code(ChannelKind.WEIXIN, "two", code="PAIR22")

    async def exercise() -> None:
        await gateway.accept(message(message_id="p1", text="/pair PAIR11"))
        await gateway.accept(
            message(
                channel=ChannelKind.WEIXIN,
                account_id="wx-account",
                user_id="wx-user",
                message_id="p2",
                text="/pair PAIR22",
            )
        )
        await gateway.accept(message(message_id="f1", text="one"))
        await gateway.accept(
            message(
                channel=ChannelKind.WEIXIN,
                account_id="wx-account",
                user_id="wx-user",
                message_id="w1",
                text="two",
            )
        )
        await asyncio.gather(
            gateway.run_pending_once(owner="worker-1"),
            gateway.run_pending_once(owner="worker-2"),
        )

    asyncio.run(exercise())

    assert Counter.maximum == 2

    Counter.maximum = 0

    async def same_session() -> None:
        await gateway.accept(message(message_id="f2", text="same-a"))
        await gateway.accept(message(message_id="f3", text="same-b"))
        await asyncio.gather(
            gateway.run_pending_once(owner="worker-1"),
            gateway.run_pending_once(owner="worker-2"),
        )

    asyncio.run(same_session())
    assert Counter.maximum == 1


def test_mobile_permission_is_bound_and_allow_once_resumes_same_session(
    tmp_path: Path,
) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: 100.0)
    adapter = FakeAdapter(ChannelKind.WEIXIN)
    runner = PermissionRunner()
    project = (tmp_path / "repo").resolve()
    project.mkdir()
    config = ChannelConfig(
        default_project="demo",
        projects={"demo": ProjectBinding(alias="demo", path=project)},
    )
    gateway = ChannelGateway(
        adapters={ChannelKind.WEIXIN: adapter},
        state=state,
        config=config,
        runtime_factory=lambda binding, session_id: runner,
        queue_path=tmp_path / "queue.sqlite3",
    )
    state.create_pair_code(ChannelKind.WEIXIN, "demo", code="PAIR12")
    asyncio.run(
        gateway.accept(
            message(channel=ChannelKind.WEIXIN, message_id="pair", text="/pair PAIR12")
        )
    )
    asyncio.run(gateway.accept(message(channel=ChannelKind.WEIXIN, message_id="task")))
    asyncio.run(gateway.run_pending_once())
    approval_text = adapter.sent[-1][1]
    code = approval_text.split("/approve ", 1)[1].split("`", 1)[0]

    asyncio.run(
        gateway.accept(
            message(
                channel=ChannelKind.WEIXIN,
                message_id="approve",
                text=f"/approve {code}",
            )
        )
    )

    assert runner.resumed == [("permission-1", "allow_once")]
    assert adapter.sent[-1][1] == "approved"


def test_approval_locks_after_five_wrong_codes_and_expires(tmp_path: Path) -> None:
    now = [100.0]
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: now[0])
    inbound = message(channel=ChannelKind.WEIXIN)
    state.create_approval(
        message=inbound,
        project_alias="demo",
        session_id="session",
        request_id="request",
        tool_name="write",
        action="write_path",
        target="README.md",
        action_hash="a" * 64,
        code="123456",
    )
    for _ in range(5):
        assert state.resolve_approval(message=inbound, code="000000", allow=True) is None
    assert state.resolve_approval(message=inbound, code="123456", allow=True) is None

    state.create_approval(
        message=inbound,
        project_alias="demo",
        session_id="session",
        request_id="request-2",
        tool_name="shell",
        action="execute_shell",
        target="pytest",
        action_hash="b" * 64,
        ttl_seconds=5,
        code="654321",
    )
    now[0] = 106.0
    assert state.resolve_approval(message=inbound, code="654321", allow=True) is None


def test_approval_is_bound_to_original_private_conversation(tmp_path: Path) -> None:
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: 100.0)
    inbound = message(channel=ChannelKind.FEISHU, conversation_id="private-chat")
    state.create_approval(
        message=inbound,
        project_alias="demo",
        session_id="session",
        request_id="request",
        tool_name="write",
        action="write_path",
        target="README.md",
        action_hash="a" * 64,
        code="123456",
    )

    assert (
        state.resolve_approval(
            message=message(
                channel=ChannelKind.FEISHU,
                conversation_id="forwarded-group",
            ),
            code="123456",
            allow=True,
            expected_action_hash="a" * 64,
        )
        is None
    )
    assert (
        state.resolve_approval(
            message=inbound,
            code="123456",
            allow=True,
            expected_action_hash="a" * 64,
        )
        is not None
    )


def test_expired_mobile_approval_resumes_as_deny_with_tool_result(
    tmp_path: Path,
) -> None:
    now = [100.0]
    state = ChannelStateStore(tmp_path / "state.sqlite3", clock=lambda: now[0])
    adapter = FakeAdapter(ChannelKind.WEIXIN)
    runner = PermissionRunner()
    project = (tmp_path / "repo").resolve()
    project.mkdir()
    gateway = ChannelGateway(
        adapters={ChannelKind.WEIXIN: adapter},
        state=state,
        config=ChannelConfig(
            default_project="demo",
            projects={"demo": ProjectBinding(alias="demo", path=project)},
        ),
        runtime_factory=lambda binding, session_id: runner,
        queue_path=tmp_path / "queue.sqlite3",
    )
    state.create_pair_code(ChannelKind.WEIXIN, "demo", code="PAIR12")
    asyncio.run(
        gateway.accept(
            message(channel=ChannelKind.WEIXIN, message_id="pair", text="/pair PAIR12")
        )
    )
    asyncio.run(gateway.accept(message(channel=ChannelKind.WEIXIN, message_id="task")))
    asyncio.run(gateway.run_pending_once())

    now[0] = 401.0
    assert asyncio.run(gateway.recover_expired_approvals()) == 1

    assert runner.resumed == [("permission-1", "deny")]
    assert "按拒绝处理" in adapter.sent[-1][1]

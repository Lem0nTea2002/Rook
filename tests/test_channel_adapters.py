from __future__ import annotations

import asyncio
import base64
import json

import pytest

from rook_agent.channels.feishu import (
    FeishuAdapter,
    _dispatch_callback,
    normalize_feishu_event,
)
from rook_agent.channels.models import ChannelKind
from rook_agent.channels.weixin import (
    WeixinAdapter,
    WeixinCredentials,
    WeixinLoginClient,
    WeixinReloginRequired,
)


class FakeWeixinHttp:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict, dict]] = []

    def request(self, method, url, *, headers, payload, timeout):
        self.requests.append((method, url, dict(headers), dict(payload)))
        return self.responses.pop(0)


def test_weixin_poll_normalizes_direct_text_and_persists_cursor(tmp_path) -> None:
    http = FakeWeixinHttp(
        [
            {
                "ret": 0,
                "errcode": 0,
                "get_updates_buf": "next",
                "msgs": [
                    {
                        "message_id": 42,
                        "from_user_id": "wx-user",
                        "to_user_id": "bot",
                        "session_id": "session",
                        "group_id": "",
                        "message_type": 1,
                        "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
                        "context_token": "ctx",
                    }
                ],
            }
        ]
    )
    saved: list[str] = []
    adapter = WeixinAdapter(
        credentials=WeixinCredentials(
            bot_token="secret",
            bot_id="bot",
            ilink_user_id="owner",
            base_url="https://ilinkai.weixin.qq.com",
        ),
        http=http,
        cursor_loader=lambda: "",
        cursor_saver=saved.append,
        random_uin=lambda: 123,
    )
    received = []

    asyncio.run(adapter.poll_once(received.append))

    assert received[0].channel is ChannelKind.WEIXIN
    assert received[0].message_id == "42"
    assert received[0].text == "hello"
    assert received[0].context_token == "ctx"
    assert saved == ["next"]
    _, url, headers, payload = http.requests[0]
    assert url.endswith("/ilink/bot/getupdates")
    assert headers["Authorization"] == "Bearer secret"
    assert base64.b64decode(headers["X-WECHAT-UIN"]).decode() == "123"
    assert payload["base_info"]["channel_version"] == "0.5.0"
    assert payload["base_info"]["bot_agent"] == "Rook/0.5.0"


def test_weixin_rejects_group_and_stale_token(tmp_path) -> None:
    grouped = FakeWeixinHttp(
        [
            {
                "ret": 0,
                "errcode": 0,
                "get_updates_buf": "next",
                "msgs": [
                    {
                        "message_id": 1,
                        "from_user_id": "u",
                        "session_id": "s",
                        "group_id": "group",
                        "message_type": 1,
                        "item_list": [{"type": 1, "text_item": {"text": "ignored"}}],
                    }
                ],
            }
        ]
    )
    adapter = WeixinAdapter(
        credentials=WeixinCredentials("token", "bot", "owner", "https://example.test"),
        http=grouped,
    )
    received = []
    asyncio.run(adapter.poll_once(received.append))
    assert received == []

    stale = WeixinAdapter(
        credentials=WeixinCredentials("token", "bot", "owner", "https://example.test"),
        http=FakeWeixinHttp([{"ret": -1, "errcode": -14, "errmsg": "expired"}]),
    )
    with pytest.raises(WeixinReloginRequired):
        asyncio.run(stale.poll_once(received.append))


def test_weixin_send_and_typing_follow_official_shapes() -> None:
    http = FakeWeixinHttp(
        [
            {"ret": 0, "errcode": 0},
            {"ret": 0, "errcode": 0, "typing_ticket": "ticket"},
            {"ret": 0, "errcode": 0},
        ]
    )
    adapter = WeixinAdapter(
        credentials=WeixinCredentials("token", "bot", "owner", "https://example.test"),
        http=http,
    )

    asyncio.run(adapter.send("user", "reply", context_token="ctx"))
    asyncio.run(adapter.set_typing("user", True, context_token="ctx"))

    send_payload = http.requests[0][3]
    assert send_payload["msg"]["to_user_id"] == "user"
    assert send_payload["msg"]["context_token"] == "ctx"
    assert send_payload["msg"]["item_list"][0]["text_item"]["text"] == "reply"
    assert http.requests[2][3]["status"] == 1


def test_weixin_long_reply_is_split_without_losing_content() -> None:
    http = FakeWeixinHttp(
        [
            {"ret": 0, "errcode": 0},
            {"ret": 0, "errcode": 0},
        ]
    )
    adapter = WeixinAdapter(
        credentials=WeixinCredentials("token", "bot", "owner", "https://example.test"),
        http=http,
    )
    text = "a" * 7_999 + "\n" + "b" * 20

    asyncio.run(adapter.send("user", text, context_token="ctx"))

    parts = [
        request[3]["msg"]["item_list"][0]["text_item"]["text"]
        for request in http.requests
    ]
    assert parts == ["a" * 7_999 + "\n", "b" * 20]


def test_weixin_qr_login_uses_official_iLink_endpoints() -> None:
    http = FakeWeixinHttp(
        [
            {"qrcode": "qr-id", "qrcode_img_content": "https://qr.example"},
            {
                "status": "confirmed",
                "bot_token": "token",
                "ilink_bot_id": "bot",
                "ilink_user_id": "owner",
            },
        ]
    )
    client = WeixinLoginClient(http=http, base_url="https://ilinkai.weixin.qq.com")

    qr = client.get_qrcode()
    status = client.poll_status(qr.qrcode)

    assert qr.qrcode == "qr-id"
    assert status["status"] == "confirmed"
    assert http.requests[0][1].endswith("/ilink/bot/get_bot_qrcode?bot_type=3")
    assert http.requests[0][3] == {"local_token_list": []}
    assert http.requests[1][0] == "GET"
    assert http.requests[1][1].endswith("/ilink/bot/get_qrcode_status?qrcode=qr-id")


def test_feishu_event_normalization_accepts_only_p2p_text() -> None:
    event = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "create_time": "100000",
                "content": json.dumps({"text": "hello"}),
            },
        }
    }

    message = normalize_feishu_event(event, account_id="app")

    assert message is not None
    assert message.channel is ChannelKind.FEISHU
    assert message.user_id == "ou_user"
    assert message.text == "hello"
    event["event"]["message"]["chat_type"] = "group"
    assert normalize_feishu_event(event, account_id="app") is None


def test_feishu_card_action_becomes_bound_approval_command() -> None:
    event = {
        "header": {"event_id": "event-1"},
        "event": {
            "operator": {"open_id": "ou_user"},
            "context": {
                "open_chat_id": "oc_1",
                "open_message_id": "om_card",
            },
            "action": {
                "value": {
                    "rook_command": "approve",
                    "code": "123456",
                }
            },
        },
    }

    message = normalize_feishu_event(event, account_id="app")

    assert message is not None
    assert message.text == "/approve 123456"
    assert message.message_id == "event-1"


class FakeFeishuSdk:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []
        self.progress: list[tuple[str, bool]] = []
        self.handler = None

    async def run(self, handler):
        self.handler = handler

    async def send_text(self, conversation_id, text, *, reply_to=None):
        self.sent.append((conversation_id, text, reply_to))

    async def set_progress(self, conversation_id, active):
        self.progress.append((conversation_id, active))

    async def close(self):
        return None


def test_feishu_adapter_uses_injected_official_sdk_facade() -> None:
    sdk = FakeFeishuSdk()
    adapter = FeishuAdapter(app_id="cli_a", app_secret="secret", sdk=sdk)

    asyncio.run(adapter.send("chat", "hello", reply_to="om_message"))

    assert sdk.sent == [("chat", "hello", "om_message")]


def test_feishu_adapter_does_not_reply_to_event_id() -> None:
    sdk = FakeFeishuSdk()
    adapter = FeishuAdapter(app_id="cli_a", app_secret="secret", sdk=sdk)

    asyncio.run(adapter.send("chat", "hello", reply_to="event-id"))

    assert sdk.sent == [("chat", "hello", None)]


def test_feishu_typing_state_uses_progress_card_facade() -> None:
    sdk = FakeFeishuSdk()
    adapter = FeishuAdapter(app_id="cli_a", app_secret="secret", sdk=sdk)

    asyncio.run(adapter.set_typing("chat", True))
    asyncio.run(adapter.set_typing("chat", False))

    assert sdk.progress == [("chat", True), ("chat", False)]


def test_feishu_long_reply_is_split_without_losing_content() -> None:
    sdk = FakeFeishuSdk()
    adapter = FeishuAdapter(app_id="cli_a", app_secret="secret", sdk=sdk)
    text = "a" * 7_999 + "\n" + "b" * 20

    asyncio.run(adapter.send("chat", text, reply_to="om_message"))

    assert sdk.sent == [
        ("chat", "a" * 7_999 + "\n", "om_message"),
        ("chat", "b" * 20, None),
    ]


def test_feishu_callback_waits_for_durable_handoff() -> None:
    async def scenario() -> list[str]:
        received: list[str] = []

        async def handler(data: object) -> None:
            await asyncio.sleep(0)
            received.append(str(data))

        await asyncio.to_thread(
            _dispatch_callback,
            handler,
            "event",
            loop=asyncio.get_running_loop(),
            timeout_seconds=0.5,
        )
        return received

    assert asyncio.run(scenario()) == ["event"]


def test_feishu_callback_allows_reply_after_previous_timeout_boundary() -> None:
    async def scenario() -> list[str]:
        received: list[str] = []

        async def handler(data: object) -> None:
            await asyncio.sleep(2.6)
            received.append(str(data))

        await asyncio.to_thread(
            _dispatch_callback,
            handler,
            "event",
            loop=asyncio.get_running_loop(),
        )
        return received

    assert asyncio.run(scenario()) == ["event"]


def test_feishu_callback_fails_when_handoff_exceeds_boundary() -> None:
    async def scenario() -> None:
        completed = asyncio.Event()

        async def handler(data: object) -> None:
            await asyncio.sleep(0.05)
            completed.set()

        with pytest.raises(RuntimeError, match="持久处理"):
            await asyncio.to_thread(
                _dispatch_callback,
                handler,
                "event",
                loop=asyncio.get_running_loop(),
                timeout_seconds=0.01,
            )
        await asyncio.wait_for(completed.wait(), timeout=0.5)

    asyncio.run(scenario())

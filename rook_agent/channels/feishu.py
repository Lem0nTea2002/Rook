"""Feishu adapter using the official lark-oapi long-connection SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
import inspect
import json
import threading
import time
from typing import Any, Protocol
import uuid

from rook_agent.channels.base import ChannelAdapter, InboundHandler, split_channel_text
from rook_agent.channels.models import ChannelKind, InboundMessage


class FeishuSdkFacade(Protocol):
    async def run(self, handler: Callable[[object], Awaitable[None] | None]) -> None: ...

    async def send_text(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class FeishuAdapter(ChannelAdapter):
    channel = ChannelKind.FEISHU

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        sdk: FeishuSdkFacade | None = None,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        self.app_id = app_id
        self._sdk = sdk or _LarkOapiFacade(app_id, app_secret)
        self._closed = False

    async def run(self, handler: InboundHandler) -> None:
        async def receive(raw: object) -> None:
            message = normalize_feishu_event(raw, account_id=self.app_id)
            if message is not None:
                await handler(message)

        await self._sdk.run(receive)

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        context_token: str | None = None,
    ) -> None:
        for index, part in enumerate(split_channel_text(text)):
            await self._sdk.send_text(
                conversation_id,
                part,
                reply_to=reply_to if index == 0 else None,
            )

    async def set_typing(
        self,
        conversation_id: str,
        active: bool,
        *,
        context_token: str | None = None,
    ) -> None:
        progress = getattr(self._sdk, "set_progress", None)
        if callable(progress):
            await progress(conversation_id, active)

    async def send_approval(
        self,
        conversation_id: str,
        *,
        code: str,
        tool_name: str,
        action: str,
        target: str,
    ) -> None:
        card_sender = getattr(self._sdk, "send_card", None)
        if not callable(card_sender):
            await self.send(
                conversation_id,
                _approval_text(code, tool_name, action, target),
            )
            return
        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "Rook 权限审批"},
                "template": "orange",
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            f"**工具**：{tool_name}\n**动作**：{action}\n"
                            f"**目标**：{target}\n\n5 分钟后自动拒绝，仅本次有效。"
                        ),
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "允许一次"},
                        "type": "primary",
                        "value": {"rook_command": "approve", "code": code},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "danger",
                        "value": {"rook_command": "deny", "code": code},
                    },
                ]
            },
        }
        await card_sender(conversation_id, card)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._sdk.close()


def normalize_feishu_event(raw: object, *, account_id: str) -> InboundMessage | None:
    payload = raw if isinstance(raw, dict) else _event_object_to_dict(raw)
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return None
    card_action = _normalize_card_action(payload, event, account_id=account_id)
    if card_action is not None:
        return card_action
    sender = event.get("sender")
    message = event.get("message")
    if not isinstance(sender, dict) or not isinstance(message, dict):
        return None
    if sender.get("sender_type") not in {None, "user"}:
        return None
    if message.get("chat_type") != "p2p" or message.get("message_type") != "text":
        return None
    content = message.get("content")
    try:
        content_object = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        return None
    if not isinstance(content_object, dict):
        return None
    text = str(content_object.get("text") or "").strip()
    sender_id = sender.get("sender_id")
    user_id = str(sender_id.get("open_id") or "") if isinstance(sender_id, dict) else ""
    message_id = str(message.get("message_id") or "")
    chat_id = str(message.get("chat_id") or "")
    if not text or not user_id or not message_id or not chat_id:
        return None
    raw_time = str(message.get("create_time") or "")
    received_at = float(raw_time) / 1000 if raw_time.isdigit() else time.time()
    return InboundMessage(
        channel=ChannelKind.FEISHU,
        account_id=account_id,
        user_id=user_id,
        conversation_id=chat_id,
        message_id=message_id,
        text=text,
        received_at=received_at,
        direct=True,
    )


class _LarkOapiFacade:
    def __init__(self, app_id: str, app_secret: str) -> None:
        try:
            import lark_oapi as lark
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                P2CardActionTriggerResponse,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Feishu support requires `pip install \"rook-agent[im]\"`"
            ) from exc
        self.lark = lark
        self.card_response_type = P2CardActionTriggerResponse
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )
        self.ws_client: Any = None
        self._stopped: asyncio.Event | None = None
        self._progress_messages: dict[str, str] = {}

    async def run(self, handler: Callable[[object], Awaitable[None] | None]) -> None:
        loop = asyncio.get_running_loop()

        def dispatch(data: object) -> None:
            _dispatch_callback(handler, data, loop=loop)

        def card_callback(data: object) -> Any:
            dispatch(data)
            return self.card_response_type(
                {"toast": {"type": "info", "content": "Rook 正在处理审批"}}
            )

        dispatcher = (
            self.lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(dispatch)
            .register_p2_card_action_trigger(card_callback)
            .build()
        )
        self.ws_client = self.lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=dispatcher,
            log_level=self.lark.LogLevel.WARNING,
        )
        self._stopped = asyncio.Event()
        failed: asyncio.Future[None] = loop.create_future()
        client = self.ws_client

        def start() -> None:
            try:
                client.start()
            except BaseException as exc:
                loop.call_soon_threadsafe(failed.set_exception, exc)

        threading.Thread(
            target=start,
            name="rook-feishu-long-connection",
            daemon=True,
        ).start()
        stopped: asyncio.Task[bool] = asyncio.create_task(self._stopped.wait())
        waiters: set[asyncio.Future[Any]] = {failed, stopped}
        done, pending = await asyncio.wait(
            waiters,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            await task

    async def send_text(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> None:
        lark = self.lark
        content = json.dumps({"text": text}, ensure_ascii=False)
        if reply_to:
            body = (
                lark.im.v1.ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type("text")
                .uuid(uuid.uuid4().hex)
                .build()
            )
            request = (
                lark.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(body)
                .build()
            )
            response = await asyncio.to_thread(self.client.im.v1.message.reply, request)
            if not response.success():
                raise RuntimeError(
                    f"Feishu message reply failed: code={response.code} msg={response.msg}"
                )
            return
        body = (
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(conversation_id)
            .msg_type("text")
            .content(content)
            .uuid(uuid.uuid4().hex)
            .build()
        )
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        response = await asyncio.to_thread(self.client.im.v1.message.create, request)
        if not response.success():
            raise RuntimeError(
                f"Feishu message send failed: code={response.code} msg={response.msg}"
            )

    async def send_card(self, conversation_id: str, card: dict[str, Any]) -> str:
        lark = self.lark
        body = (
            lark.im.v1.CreateMessageRequestBody.builder()
            .receive_id(conversation_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .uuid(uuid.uuid4().hex)
            .build()
        )
        request = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        response = await asyncio.to_thread(self.client.im.v1.message.create, request)
        if not response.success():
            raise RuntimeError(
                f"Feishu approval card send failed: code={response.code} msg={response.msg}"
            )
        message_id = str(getattr(response.data, "message_id", "") or "")
        if not message_id:
            raise RuntimeError("Feishu card send succeeded without a message_id")
        return message_id

    async def set_progress(self, conversation_id: str, active: bool) -> None:
        if active:
            if conversation_id in self._progress_messages:
                return
            message_id = await self.send_card(
                conversation_id,
                _progress_card(active=True),
            )
            self._progress_messages[conversation_id] = message_id
            return
        progress_message_id = self._progress_messages.get(conversation_id)
        if progress_message_id is None:
            return
        del self._progress_messages[conversation_id]
        await self._patch_card(progress_message_id, _progress_card(active=False))

    async def _patch_card(self, message_id: str, card: dict[str, Any]) -> None:
        lark = self.lark
        body = (
            lark.im.v1.PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        request = (
            lark.im.v1.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = await asyncio.to_thread(self.client.im.v1.message.patch, request)
        if not response.success():
            raise RuntimeError(
                f"Feishu progress card update failed: code={response.code} msg={response.msg}"
            )

    async def close(self) -> None:
        client = self.ws_client
        if client is not None:
            disconnect = getattr(client, "_disconnect", None)
            if callable(disconnect):
                try:
                    from lark_oapi.ws.client import loop as lark_ws_loop

                    future = asyncio.run_coroutine_threadsafe(disconnect(), lark_ws_loop)
                    await asyncio.wait_for(asyncio.wrap_future(future), timeout=5)
                except Exception:
                    pass
        if self._stopped is not None:
            self._stopped.set()


def _dispatch_callback(
    handler: Callable[[object], Awaitable[None] | None],
    data: object,
    *,
    loop: asyncio.AbstractEventLoop,
    timeout_seconds: float = 2.5,
) -> None:
    """在飞书回调返回前确认事件已交给持久化网关。"""
    result = handler(data)
    if not inspect.isawaitable(result):
        return

    async def await_result() -> None:
        await result

    future = asyncio.run_coroutine_threadsafe(await_result(), loop)
    try:
        future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        raise RuntimeError("飞书事件未在 2.5 秒内完成持久交接") from exc


def _progress_card(*, active: bool) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "Rook 正在处理" if active else "Rook 处理完成",
            },
            "template": "blue" if active else "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "任务已进入本地执行队列，请稍候。"
                        if active
                        else "执行已结束，结果消息即将送达。"
                    ),
                }
            ]
        },
    }


def _event_object_to_dict(raw: object) -> dict[str, Any]:
    event = getattr(raw, "event", None)
    operator = getattr(event, "operator", None)
    action = getattr(event, "action", None)
    context = getattr(event, "context", None)
    if operator is not None and action is not None and context is not None:
        header = getattr(raw, "header", None)
        return {
            "header": {"event_id": getattr(header, "event_id", None)},
            "event": {
                "operator": {"open_id": getattr(operator, "open_id", None)},
                "action": {"value": getattr(action, "value", None)},
                "context": {
                    "open_chat_id": getattr(context, "open_chat_id", None),
                    "open_message_id": getattr(context, "open_message_id", None),
                },
            },
        }
    sender = getattr(event, "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    message = getattr(event, "message", None)
    if event is None or sender is None or message is None:
        return {}
    return {
        "event": {
            "sender": {
                "sender_type": getattr(sender, "sender_type", None),
                "sender_id": {"open_id": getattr(sender_id, "open_id", None)},
            },
            "message": {
                "message_id": getattr(message, "message_id", None),
                "chat_id": getattr(message, "chat_id", None),
                "chat_type": getattr(message, "chat_type", None),
                "message_type": getattr(message, "message_type", None),
                "create_time": getattr(message, "create_time", None),
                "content": getattr(message, "content", None),
            },
        }
    }


def _normalize_card_action(
    payload: dict[str, Any],
    event: dict[str, Any],
    *,
    account_id: str,
) -> InboundMessage | None:
    action = event.get("action")
    operator = event.get("operator")
    context = event.get("context")
    if not isinstance(action, dict) or not isinstance(operator, dict) or not isinstance(context, dict):
        return None
    value = action.get("value")
    if not isinstance(value, dict):
        return None
    command = str(value.get("rook_command") or "")
    code = str(value.get("code") or "")
    if command not in {"approve", "deny"} or len(code) != 6 or not code.isdigit():
        return None
    user_id = str(operator.get("open_id") or "")
    chat_id = str(context.get("open_chat_id") or "")
    message_id = str(context.get("open_message_id") or "")
    header = payload.get("header")
    if isinstance(header, dict):
        message_id = str(header.get("event_id") or message_id)
    if not user_id or not chat_id or not message_id:
        return None
    return InboundMessage(
        channel=ChannelKind.FEISHU,
        account_id=account_id,
        user_id=user_id,
        conversation_id=chat_id,
        message_id=message_id,
        text=f"/{command} {code}",
        received_at=time.time(),
        direct=True,
        metadata={"card_action": True},
    )


def _approval_text(code: str, tool_name: str, action: str, target: str) -> str:
    return (
        "Rook 已暂停敏感操作。\n"
        f"工具：{tool_name}\n动作：{action}\n目标：{target}\n"
        f"5 分钟内发送 `/approve {code}` 或 `/deny {code}`。仅本次有效。"
    )


__all__ = ["FeishuAdapter", "FeishuSdkFacade", "normalize_feishu_event"]

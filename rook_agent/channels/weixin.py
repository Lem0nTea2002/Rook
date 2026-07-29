"""Native Python adapter for Tencent's official WeChat iLink Bot protocol.

Protocol shapes are derived from Tencent/openclaw-weixin (MIT). Rook implements
the transport directly and does not embed or require an OpenClaw host.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
import json
import logging
import random
import ssl
import time
from typing import Any, Protocol
from urllib import request as urllib_request
import uuid

from rook_agent.channels.base import ChannelAdapter, InboundHandler, split_channel_text
from rook_agent.channels.models import ChannelKind, InboundMessage


BOT_AGENT = "Rook/0.4.0"
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
logger = logging.getLogger(__name__)


class WeixinReloginRequired(RuntimeError):
    """The iLink credential is stale and must not be retried automatically."""


@dataclass(frozen=True, slots=True)
class WeixinCredentials:
    bot_token: str
    bot_id: str
    ilink_user_id: str
    base_url: str = DEFAULT_BASE_URL

    def __post_init__(self) -> None:
        if not self.bot_token or not self.bot_id or not self.ilink_user_id:
            raise ValueError("incomplete WeChat iLink credentials")
        if not self.base_url.startswith("https://"):
            raise ValueError("WeChat iLink base URL must use HTTPS")


class WeixinHttp(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]: ...


class UrllibWeixinHttp:
    """Small stdlib JSON transport with certificate validation enabled."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if method != "GET"
            else None
        )
        req = urllib_request.Request(url, data=body, method=method, headers=headers)
        with urllib_request.urlopen(
            req,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as response:
            data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("WeChat iLink response exceeds 4 MiB")
        decoded = json.loads(data.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("WeChat iLink response must be a JSON object")
        return decoded


class WeixinAdapter(ChannelAdapter):
    channel = ChannelKind.WEIXIN

    def __init__(
        self,
        *,
        credentials: WeixinCredentials,
        http: WeixinHttp | None = None,
        cursor_loader: Callable[[], str] | None = None,
        cursor_saver: Callable[[str], None] | None = None,
        random_uin: Callable[[], int] | None = None,
    ) -> None:
        self.credentials = credentials
        self.http = http or UrllibWeixinHttp()
        self.cursor_loader = cursor_loader or (lambda: "")
        self.cursor_saver = cursor_saver or (lambda value: None)
        self.random_uin = random_uin or (lambda: random.SystemRandom().randrange(0, 2**32))
        self._closed = False
        self._context_tokens: dict[str, str] = {}
        self._typing_tickets: dict[str, str] = {}

    async def run(self, handler: InboundHandler) -> None:
        failures = 0
        while not self._closed:
            try:
                await self.poll_once(handler)
                if failures:
                    logger.info(
                        "weixin channel reconnected",
                        extra={"event": "channel_reconnected", "failures": failures},
                    )
                failures = 0
            except WeixinReloginRequired:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                delay = 2.0 if failures <= 3 else min(30.0, 2.0 ** min(failures - 2, 5))
                logger.warning(
                    "weixin channel poll failed",
                    extra={
                        "event": "channel_reconnect_scheduled",
                        "failure_type": type(exc).__name__,
                        "failures": failures,
                        "delay_seconds": delay,
                    },
                )
                await asyncio.sleep(delay)

    async def poll_once(
        self,
        handler: Callable[[InboundMessage], Awaitable[None] | None],
    ) -> None:
        payload = {
            "get_updates_buf": self.cursor_loader() or "",
            "base_info": _base_info(),
        }
        response = await self._request("POST", "ilink/bot/getupdates", payload, timeout=40)
        if int(response.get("errcode") or 0) == -14:
            raise WeixinReloginRequired("WeChat iLink token expired; run `rook channel login weixin`")
        _require_success(response, operation="getupdates")
        next_cursor = str(response.get("get_updates_buf") or "")
        messages = response.get("msgs")
        if not isinstance(messages, list):
            if next_cursor:
                self.cursor_saver(next_cursor)
            return
        for raw in messages:
            message = _normalize_weixin_message(raw, account_id=self.credentials.bot_id)
            if message is None:
                continue
            if message.context_token:
                self._context_tokens[message.conversation_id] = message.context_token
            result = handler(message)
            if inspect.isawaitable(result):
                await result
        # Advance only after every message was handed to the durable gateway.
        # A crash before this line replays messages; SQLite deduplication makes
        # that safe. Advancing first could lose an unqueued message.
        if next_cursor:
            self.cursor_saver(next_cursor)

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        context_token: str | None = None,
    ) -> None:
        token = context_token or self._context_tokens.get(conversation_id)
        if not token:
            raise ValueError("WeChat reply requires the inbound context_token")
        for part in split_channel_text(text):
            payload = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": conversation_id,
                    "client_id": uuid.uuid4().hex,
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [{"type": 1, "text_item": {"text": part}}],
                    "context_token": token,
                },
                "base_info": _base_info(),
            }
            response = await self._request(
                "POST",
                "ilink/bot/sendmessage",
                payload,
                timeout=15,
            )
            _require_success(response, operation="sendmessage")

    async def set_typing(
        self,
        conversation_id: str,
        active: bool,
        *,
        context_token: str | None = None,
    ) -> None:
        token = context_token or self._context_tokens.get(conversation_id)
        ticket = self._typing_tickets.get(conversation_id)
        if ticket is None:
            config = await self._request(
                "POST",
                "ilink/bot/getconfig",
                {
                    "ilink_user_id": conversation_id,
                    "context_token": token or "",
                    "base_info": _base_info(),
                },
                timeout=15,
            )
            _require_success(config, operation="getconfig")
            ticket = str(config.get("typing_ticket") or "")
            if not ticket:
                return
            self._typing_tickets[conversation_id] = ticket
        response = await self._request(
            "POST",
            "ilink/bot/sendtyping",
            {
                "ilink_user_id": conversation_id,
                "typing_ticket": ticket,
                "status": 1 if active else 2,
                "base_info": _base_info(),
            },
            timeout=15,
        )
        _require_success(response, operation="sendtyping")

    async def close(self) -> None:
        self._closed = True

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.http.request,
            method,
            f"{self.credentials.base_url.rstrip('/')}/{endpoint}",
            headers=self._headers(),
            payload=payload,
            timeout=timeout,
        )

    def _headers(self) -> dict[str, str]:
        uin = base64.b64encode(str(self.random_uin()).encode("ascii")).decode("ascii")
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.credentials.bot_token}",
            "X-WECHAT-UIN": uin,
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": "1",
        }


@dataclass(frozen=True, slots=True)
class WeixinQrCode:
    qrcode: str
    image_content: str


class WeixinLoginClient:
    """Official QR login flow; callers persist confirmed secrets in keyring."""

    def __init__(
        self,
        *,
        http: WeixinHttp | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.http = http or UrllibWeixinHttp()
        self.base_url = base_url.rstrip("/")

    def get_qrcode(self) -> WeixinQrCode:
        response = self.http.request(
            "POST",
            f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type=3",
            headers={"Content-Type": "application/json"},
            payload={"local_token_list": []},
            timeout=15,
        )
        qrcode = str(response.get("qrcode") or "")
        content = str(response.get("qrcode_img_content") or "")
        if not qrcode or not content:
            raise ValueError("WeChat iLink QR response is incomplete")
        return WeixinQrCode(qrcode=qrcode, image_content=content)

    def poll_status(
        self,
        qrcode: str,
        *,
        verify_code: str | None = None,
    ) -> dict[str, Any]:
        suffix = f"?qrcode={qrcode}"
        if verify_code:
            suffix += f"&verify_code={verify_code}"
        return self.http.request(
            "GET",
            f"{self.base_url}/ilink/bot/get_qrcode_status{suffix}",
            headers={},
            payload={},
            timeout=40,
        )


def _normalize_weixin_message(
    raw: object,
    *,
    account_id: str,
) -> InboundMessage | None:
    if not isinstance(raw, dict):
        return None
    if int(raw.get("message_type") or 0) != 1:
        return None
    if str(raw.get("group_id") or ""):
        return None
    items = raw.get("item_list")
    if not isinstance(items, list):
        return None
    texts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or int(item.get("type") or 0) != 1:
            continue
        text_item = item.get("text_item")
        if isinstance(text_item, dict) and isinstance(text_item.get("text"), str):
            texts.append(text_item["text"])
    text = "\n".join(texts).strip()
    if not text:
        return None
    user_id = str(raw.get("from_user_id") or "")
    session_id = str(raw.get("session_id") or user_id)
    message_id = str(raw.get("message_id") or "")
    if not user_id or not session_id or not message_id:
        return None
    return InboundMessage(
        channel=ChannelKind.WEIXIN,
        account_id=account_id,
        user_id=user_id,
        conversation_id=user_id,
        message_id=message_id,
        text=text,
        received_at=time.time(),
        context_token=str(raw.get("context_token") or "") or None,
        direct=True,
        metadata={"session_id": session_id},
    )


def _base_info() -> dict[str, Any]:
    return {"channel_version": "0.4.0", "bot_agent": BOT_AGENT}


def _require_success(response: dict[str, Any], *, operation: str) -> None:
    ret = int(response.get("ret") or 0)
    errcode = int(response.get("errcode") or 0)
    if ret != 0 or errcode != 0:
        raise RuntimeError(f"WeChat iLink {operation} failed: ret={ret} errcode={errcode}")


__all__ = [
    "BOT_AGENT",
    "DEFAULT_BASE_URL",
    "UrllibWeixinHttp",
    "WeixinAdapter",
    "WeixinCredentials",
    "WeixinLoginClient",
    "WeixinQrCode",
    "WeixinReloginRequired",
]

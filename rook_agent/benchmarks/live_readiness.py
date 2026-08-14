"""付费 Benchmark 启动前的零模型网络门禁。"""

from __future__ import annotations

from dataclasses import dataclass
import socket
import time
from typing import Callable
from urllib.parse import urlsplit

from rook_agent.evolution.gate import redact_sensitive_text


_ATTEMPTS = 3
_CONNECT_TIMEOUT_SECONDS = 5.0
_INTERVAL_SECONDS = 0.25
_MAX_ERROR_MESSAGE_CHARS = 1_000


@dataclass(frozen=True, slots=True)
class EndpointReadinessReceipt:
    host: str
    port: int
    successful_attempts: int


class EndpointReadinessError(RuntimeError):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        attempt: int,
        exception_type: str,
        message: str,
    ) -> None:
        sanitized = _bounded_redacted(message)
        super().__init__(
            f"Benchmark 网络 readiness 第 {attempt}/{_ATTEMPTS} 轮失败："
            f"{host}:{port} {exception_type}: {sanitized}"
        )
        self.host = host
        self.port = port
        self.attempt = attempt
        self.exception_type = exception_type
        self.message = sanitized


def verify_endpoint_readiness(
    endpoint: str,
    *,
    resolver: Callable[..., object] | None = None,
    connector: Callable[..., object] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> EndpointReadinessReceipt:
    """连续验证 DNS 与 TCP；任意一轮失败都禁止创建付费实验。"""

    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Benchmark Provider endpoint 必须是有效的 HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Benchmark Provider endpoint 禁止包含凭据")
    host = parsed.hostname
    port = parsed.port or 443
    resolve = resolver or socket.getaddrinfo
    connect = connector or socket.create_connection

    for attempt in range(1, _ATTEMPTS + 1):
        connection = None
        try:
            addresses = resolve(host, port, type=socket.SOCK_STREAM)
            if not addresses:
                raise OSError("DNS 解析没有返回可连接地址")
            connection = connect(
                (host, port),
                _CONNECT_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            root = _deepest_exception(exc)
            raise EndpointReadinessError(
                host=host,
                port=port,
                attempt=attempt,
                exception_type=(f"{type(root).__module__}.{type(root).__qualname__}"),
                message=str(root) or "基础设施异常未提供错误信息",
            ) from exc
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        if attempt < _ATTEMPTS:
            sleeper(_INTERVAL_SECONDS)

    return EndpointReadinessReceipt(
        host=host,
        port=port,
        successful_attempts=_ATTEMPTS,
    )


def _deepest_exception(error: BaseException) -> BaseException:
    current = error
    seen = {id(current)}
    for _ in range(8):
        next_error = current.__cause__ or current.__context__
        if next_error is None or id(next_error) in seen:
            break
        seen.add(id(next_error))
        current = next_error
    return current


def _bounded_redacted(message: str) -> str:
    redacted = redact_sensitive_text(message)
    if len(redacted) <= _MAX_ERROR_MESSAGE_CHARS:
        return redacted
    suffix = "…[truncated]"
    return redacted[: _MAX_ERROR_MESSAGE_CHARS - len(suffix)] + suffix


__all__ = [
    "EndpointReadinessError",
    "EndpointReadinessReceipt",
    "verify_endpoint_readiness",
]

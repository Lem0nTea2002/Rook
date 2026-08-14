from __future__ import annotations

from types import SimpleNamespace

import pytest

from rook_agent.benchmarks import cli
from rook_agent.benchmarks.live_readiness import (
    EndpointReadinessError,
    verify_endpoint_readiness,
)


class _Connection:
    def __init__(self, closed: list[bool]) -> None:
        self._closed = closed

    def close(self) -> None:
        self._closed.append(True)


def test_endpoint_readiness_requires_three_fresh_dns_and_tcp_successes() -> None:
    resolved: list[tuple[str, int]] = []
    connected: list[tuple[tuple[str, int], float]] = []
    closed: list[bool] = []
    sleeps: list[float] = []

    def resolver(host: str, port: int, *, type: int):
        resolved.append((host, port))
        return [(2, type, 6, "", ("203.0.113.10", port))]

    def connector(address: tuple[str, int], timeout: float):
        connected.append((address, timeout))
        return _Connection(closed)

    receipt = verify_endpoint_readiness(
        "https://api.deepseek.com/v1",
        resolver=resolver,
        connector=connector,
        sleeper=sleeps.append,
    )

    assert receipt.host == "api.deepseek.com"
    assert receipt.port == 443
    assert receipt.successful_attempts == 3
    assert resolved == [("api.deepseek.com", 443)] * 3
    assert connected == [(("api.deepseek.com", 443), 5.0)] * 3
    assert closed == [True, True, True]
    assert sleeps == [0.25, 0.25]


def test_endpoint_readiness_fails_on_first_unstable_attempt() -> None:
    calls = 0

    def resolver(host: str, port: int, *, type: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(11001, "getaddrinfo failed")
        return [(2, type, 6, "", ("203.0.113.10", port))]

    with pytest.raises(EndpointReadinessError) as caught:
        verify_endpoint_readiness(
            "https://api.deepseek.com",
            resolver=resolver,
            connector=lambda _address, _timeout: _Connection([]),
            sleeper=lambda _seconds: None,
        )

    assert calls == 2
    assert caught.value.attempt == 2
    assert caught.value.exception_type == "builtins.OSError"
    assert "getaddrinfo failed" in caught.value.message


def test_memory_run_stops_at_readiness_before_provider_or_experiment(
    monkeypatch,
) -> None:
    reached: list[str] = []

    def fail_readiness(_project_root):
        reached.append("readiness")
        raise EndpointReadinessError(
            host="api.deepseek.com",
            port=443,
            attempt=1,
            exception_type="socket.gaierror",
            message="getaddrinfo failed",
        )

    monkeypatch.setattr(cli, "_require_memory_endpoint_readiness", fail_readiness)
    args = SimpleNamespace(
        memory_command="run",
        allow_external=True,
        allow_costs=True,
        project=".",
    )

    with pytest.raises(EndpointReadinessError):
        cli._run_memory(args)

    assert reached == ["readiness"]

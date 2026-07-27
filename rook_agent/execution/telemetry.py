"""Tracing adapters for in-memory verification and OTLP production export."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
import threading
import time
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class SpanRecord:
    name: str
    attributes: Mapping[str, Any]
    status: str
    duration_seconds: float
    error_type: str | None


class TraceSink(Protocol):
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any],
    ) -> AbstractContextManager[Any]:
        ...


class NoopTracer:
    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any],
    ) -> Iterator[None]:
        yield


class InMemoryTracer:
    def __init__(self) -> None:
        self._records: list[SpanRecord] = []
        self._lock = threading.Lock()

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any],
    ) -> Iterator[None]:
        started = time.monotonic()
        status = "ok"
        error_type = None
        try:
            yield
        except BaseException as exc:
            status = "error"
            error_type = type(exc).__name__
            raise
        finally:
            record = SpanRecord(
                name=name,
                attributes=MappingProxyType(dict(attributes)),
                status=status,
                duration_seconds=time.monotonic() - started,
                error_type=error_type,
            )
            with self._lock:
                self._records.append(record)

    def records(self) -> tuple[SpanRecord, ...]:
        with self._lock:
            return tuple(self._records)


class OpenTelemetryTracer:
    """OTLP/HTTP tracer loaded only when the scale extra is installed."""

    def __init__(
        self,
        *,
        service_name: str = "rook-execution",
        endpoint: str | None = None,
    ) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:
            raise RuntimeError(
                "OpenTelemetry tracing requires: pip install 'rook-agent[scale]'"
            ) from exc
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        self._provider = provider
        self._tracer = trace.get_tracer(
            "rook_agent.execution",
            tracer_provider=provider,
        )

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, Any],
    ) -> AbstractContextManager[Any]:
        return self._tracer.start_as_current_span(
            name,
            attributes=dict(attributes),
        )

    def shutdown(self) -> None:
        self._provider.shutdown()


__all__ = [
    "InMemoryTracer",
    "NoopTracer",
    "OpenTelemetryTracer",
    "SpanRecord",
    "TraceSink",
]

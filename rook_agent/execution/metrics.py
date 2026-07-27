"""Dependency-free metrics core with Prometheus and optional OTel adapters."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import re
import threading
from typing import Any, Mapping, Protocol


_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\Z")


@dataclass(frozen=True, slots=True)
class MetricSample:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float


@dataclass(frozen=True, slots=True)
class HistogramSample:
    name: str
    labels: tuple[tuple[str, str], ...]
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    counters: tuple[MetricSample, ...]
    gauges: tuple[MetricSample, ...]
    histograms: tuple[HistogramSample, ...]


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        ...

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        ...

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        ...


class InMemoryMetrics:
    """Thread-safe metric recorder used by workers, tests, and reports."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], list[float]
        ] = defaultdict(list)

    def increment(
        self,
        name: str,
        value: float = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        if value < 0:
            raise ValueError("counter increments must not be negative")
        with self._lock:
            self._counters[key] += float(value)

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        if not math.isfinite(value):
            raise ValueError("histogram value must be finite")
        with self._lock:
            self._histograms[key].append(float(value))

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        if not math.isfinite(value):
            raise ValueError("gauge value must be finite")
        with self._lock:
            self._gauges[key] = float(value)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = tuple(
                MetricSample(name=name, labels=labels, value=value)
                for (name, labels), value in sorted(self._counters.items())
            )
            gauges = tuple(
                MetricSample(name=name, labels=labels, value=value)
                for (name, labels), value in sorted(self._gauges.items())
            )
            histograms = tuple(
                HistogramSample(name=name, labels=labels, values=tuple(values))
                for (name, labels), values in sorted(self._histograms.items())
            )
        return MetricsSnapshot(
            counters=counters,
            gauges=gauges,
            histograms=histograms,
        )


def render_prometheus(snapshot: MetricsSnapshot) -> str:
    """Render deterministic Prometheus text exposition without an HTTP dependency."""

    lines: list[str] = []
    for sample in snapshot.counters:
        lines.append(f"{sample.name}{_render_labels(sample.labels)} {_number(sample.value)}")
    for sample in snapshot.gauges:
        lines.append(f"{sample.name}{_render_labels(sample.labels)} {_number(sample.value)}")
    for histogram in snapshot.histograms:
        count = len(histogram.values)
        total = sum(histogram.values)
        lines.append(
            f"{histogram.name}_count{_render_labels(histogram.labels)} {count}"
        )
        lines.append(
            f"{histogram.name}_sum{_render_labels(histogram.labels)} {_number(total)}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


class OpenTelemetryMetrics:
    """Optional OpenTelemetry metric adapter loaded only with ``rook-agent[scale]``."""

    def __init__(self, *, service_name: str = "rook-execution") -> None:
        try:
            from opentelemetry import metrics
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:
            raise RuntimeError(
                "OpenTelemetry support requires: pip install 'rook-agent[scale]'"
            ) from exc
        provider = MeterProvider(
            resource=Resource.create({"service.name": service_name})
        )
        metrics.set_meter_provider(provider)
        self._meter = metrics.get_meter("rook_agent.execution")
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def increment(
        self,
        name: str,
        value: float = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        counter = self._counters.setdefault(name, self._meter.create_counter(name))
        counter.add(value, attributes=dict(labels or {}))

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        histogram = self._histograms.setdefault(
            name,
            self._meter.create_histogram(name),
        )
        histogram.record(value, attributes=dict(labels or {}))

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self._gauges[(name, tuple(sorted((labels or {}).items())))] = float(value)


class PrometheusMetrics:
    """Official Prometheus client adapter with an optional HTTP endpoint."""

    def __init__(self, *, port: int | None = None, address: str = "127.0.0.1") -> None:
        try:
            from prometheus_client import (
                CollectorRegistry,
                Counter,
                Gauge,
                Histogram,
                start_http_server,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Prometheus support requires: pip install 'rook-agent[scale]'"
            ) from exc
        self._registry = CollectorRegistry()
        self._counter_type = Counter
        self._gauge_type = Gauge
        self._histogram_type = Histogram
        self._lock = threading.Lock()
        self._counters: dict[str, tuple[Any, tuple[str, ...]]] = {}
        self._gauges: dict[str, tuple[Any, tuple[str, ...]]] = {}
        self._histograms: dict[str, tuple[Any, tuple[str, ...]]] = {}
        self._server = (
            start_http_server(port, addr=address, registry=self._registry)
            if port is not None
            else None
        )

    def increment(
        self,
        name: str,
        value: float = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        metric = self._metric(self._counters, self._counter_type, name, labels)
        _prometheus_child(metric, labels).inc(value)

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        metric = self._metric(self._histograms, self._histogram_type, name, labels)
        _prometheus_child(metric, labels).observe(value)

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        metric = self._metric(self._gauges, self._gauge_type, name, labels)
        _prometheus_child(metric, labels).set(value)

    def _metric(
        self,
        cache: dict[str, tuple[Any, tuple[str, ...]]],
        metric_type: Any,
        name: str,
        labels: Mapping[str, str] | None,
    ) -> Any:
        label_names = tuple(sorted((labels or {}).keys()))
        with self._lock:
            existing = cache.get(name)
            if existing is not None:
                metric, expected = existing
                if expected != label_names:
                    raise ValueError(f"Prometheus label set changed for metric: {name}")
                return metric
            metric = metric_type(
                name,
                f"Rook execution metric {name}.",
                labelnames=label_names,
                registry=self._registry,
            )
            cache[name] = (metric, label_names)
            return metric

    @property
    def registry(self) -> Any:
        return self._registry


def _metric_key(
    name: str,
    labels: Mapping[str, str] | None,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    if not _METRIC_NAME.fullmatch(name):
        raise ValueError(f"invalid metric name: {name}")
    normalized = tuple(sorted((str(key), str(value)) for key, value in (labels or {}).items()))
    return name, normalized


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(
        f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for key, value in labels
    )
    return "{" + rendered + "}"


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, ".12g")


def _prometheus_child(
    metric: Any,
    labels: Mapping[str, str] | None,
) -> Any:
    return metric.labels(**dict(labels or {})) if labels else metric


__all__ = [
    "InMemoryMetrics",
    "MetricSample",
    "MetricsSink",
    "MetricsSnapshot",
    "OpenTelemetryMetrics",
    "PrometheusMetrics",
    "render_prometheus",
]

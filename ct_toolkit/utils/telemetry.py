"""
ct_toolkit.utils.telemetry
--------------------------
OpenTelemetry (OTel) integration and lightweight in-memory metric/span recorder
for enterprise observability.

Exports standard metrics & span attributes:
  - ct.divergence.l1_score
  - ct.divergence.tier
  - ct.policy.blocked
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable, Iterator

from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)

try:
    from opentelemetry import metrics as otel_metrics  # type: ignore[import-not-found]
    from opentelemetry import trace as otel_trace  # type: ignore[import-not-found]

    _OTEL_AVAILABLE = True
except ImportError:
    otel_metrics = None
    otel_trace = None
    _OTEL_AVAILABLE = False


@dataclass
class RecordedSpan:
    """Represents a completed telemetry span."""
    name: str
    start_time: float
    end_time: float
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"

    @property
    def duration_ms(self) -> float:
        return round((self.end_time - self.start_time) * 1000.0, 3)


class TelemetryManager:
    """
    Unified telemetry manager that bridges to OpenTelemetry (when installed)
    and maintains thread-safe in-memory metrics/spans for inspection and testing.
    """

    METRIC_L1_SCORE = "ct.divergence.l1_score"
    METRIC_TIER = "ct.divergence.tier"
    METRIC_POLICY_BLOCKED = "ct.policy.blocked"

    def __init__(
        self,
        service_name: str = "ct-toolkit",
        enabled: bool = True,
        on_metric_callback: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> None:
        self.service_name = service_name
        self.enabled = enabled
        self._on_metric_callback = on_metric_callback
        self._lock = threading.RLock()

        # In-memory metric store
        self._l1_scores: list[float] = []
        self._tier_counts: dict[str, int] = {}
        self._policy_blocked_count: int = 0
        self._spans: list[RecordedSpan] = []

        # Optional OpenTelemetry instruments
        self._tracer = None
        self._meter = None
        self._otel_l1_histogram = None
        self._otel_tier_counter = None
        self._otel_blocked_counter = None

        if self.enabled and _OTEL_AVAILABLE:
            try:
                self._tracer = otel_trace.get_tracer(service_name)
                self._meter = otel_metrics.get_meter(service_name)
                self._otel_l1_histogram = self._meter.create_histogram(
                    name=self.METRIC_L1_SCORE,
                    description="L1 Embedding Cosine Similarity divergence score",
                    unit="1",
                )
                self._otel_tier_counter = self._meter.create_counter(
                    name=self.METRIC_TIER,
                    description="Count of divergence evaluations by tier",
                    unit="1",
                )
                self._otel_blocked_counter = self._meter.create_counter(
                    name=self.METRIC_POLICY_BLOCKED,
                    description="Count of policy/guardrail blocked interactions",
                    unit="1",
                )
            except Exception as exc:
                logger.debug(f"OpenTelemetry initialization skipped: {exc}")

    @property
    def otel_available(self) -> bool:
        return _OTEL_AVAILABLE

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Context manager that records a span in OTel (if available) and in-memory."""
        span_attrs: dict[str, Any] = dict(attributes or {})
        if not self.enabled:
            yield span_attrs
            return

        start = time.time()
        status = "OK"

        if self._tracer is not None:
            with self._tracer.start_as_current_span(name) as otel_span:
                try:
                    yield span_attrs
                except Exception as exc:
                    status = f"ERROR: {type(exc).__name__}"
                    try:
                        otel_span.record_exception(exc)
                    except Exception:
                        pass
                    raise
                finally:
                    for k, v in span_attrs.items():
                        if v is not None:
                            try:
                                otel_span.set_attribute(k, v)
                            except Exception:
                                pass
                    end = time.time()
                    with self._lock:
                        self._spans.append(
                            RecordedSpan(
                                name=name,
                                start_time=start,
                                end_time=end,
                                attributes=dict(span_attrs),
                                status=status,
                            )
                        )
        else:
            try:
                yield span_attrs
            except Exception as exc:
                status = f"ERROR: {type(exc).__name__}"
                raise
            finally:
                end = time.time()
                with self._lock:
                    self._spans.append(
                        RecordedSpan(
                            name=name,
                            start_time=start,
                            end_time=end,
                            attributes=dict(span_attrs),
                            status=status,
                        )
                    )

    def record_divergence(
        self,
        *,
        l1_score: float | None,
        tier: str,
        blocked: bool = False,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Records `ct.divergence.l1_score`, `ct.divergence.tier`, and `ct.policy.blocked`."""
        if not self.enabled:
            return

        attrs = dict(attributes or {})
        attrs["tier"] = str(tier)

        with self._lock:
            if l1_score is not None:
                self._l1_scores.append(float(l1_score))
            self._tier_counts[str(tier)] = self._tier_counts.get(str(tier), 0) + 1
            if blocked:
                self._policy_blocked_count += 1

        if self._otel_l1_histogram is not None and l1_score is not None:
            try:
                self._otel_l1_histogram.record(float(l1_score), attributes=attrs)
            except Exception:
                pass

        if self._otel_tier_counter is not None:
            try:
                self._otel_tier_counter.add(1, attributes=attrs)
            except Exception:
                pass

        if blocked and self._otel_blocked_counter is not None:
            try:
                self._otel_blocked_counter.add(1, attributes=attrs)
            except Exception:
                pass

        if self._on_metric_callback is not None:
            try:
                if l1_score is not None:
                    self._on_metric_callback(self.METRIC_L1_SCORE, float(l1_score), attrs)
                self._on_metric_callback(self.METRIC_TIER, 1.0, attrs)
                if blocked:
                    self._on_metric_callback(self.METRIC_POLICY_BLOCKED, 1.0, attrs)
            except Exception as exc:
                logger.warning(f"Telemetry metric callback failed: {exc}")

    def record_policy_blocked(self, reason: str = "", attributes: dict[str, Any] | None = None) -> None:
        """Records a policy block event (`ct.policy.blocked`)."""
        if not self.enabled:
            return

        attrs = dict(attributes or {})
        if reason:
            attrs["reason"] = reason

        with self._lock:
            self._policy_blocked_count += 1

        if self._otel_blocked_counter is not None:
            try:
                self._otel_blocked_counter.add(1, attributes=attrs)
            except Exception:
                pass

        if self._on_metric_callback is not None:
            try:
                self._on_metric_callback(self.METRIC_POLICY_BLOCKED, 1.0, attrs)
            except Exception as exc:
                logger.warning(f"Telemetry metric callback failed: {exc}")

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """Returns a snapshot of collected telemetry metrics."""
        with self._lock:
            avg_l1 = (
                sum(self._l1_scores) / len(self._l1_scores)
                if self._l1_scores
                else 0.0
            )
            return {
                self.METRIC_L1_SCORE: {
                    "count": len(self._l1_scores),
                    "mean": round(avg_l1, 6),
                    "values": list(self._l1_scores),
                },
                self.METRIC_TIER: dict(self._tier_counts),
                self.METRIC_POLICY_BLOCKED: self._policy_blocked_count,
                "spans_recorded": len(self._spans),
            }

    @property
    def spans(self) -> list[RecordedSpan]:
        with self._lock:
            return list(self._spans)

    def reset(self) -> None:
        """Clears in-memory telemetry buffers."""
        with self._lock:
            self._l1_scores.clear()
            self._tier_counts.clear()
            self._policy_blocked_count = 0
            self._spans.clear()

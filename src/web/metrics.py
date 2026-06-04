"""Dependency-free Prometheus metrics for the web tier (REVUE-362).

Fly.io's managed Prometheus scrapes ``GET /metrics``; Grafana alert rules and
the dashboard read the series rendered here. We emit the Prometheus text
exposition format (v0.0.4) directly rather than pulling in ``prometheus_client``
so the web container's dependency surface (and its Docker image) stays minimal
and so this module is unit-testable without a process-global registry.

Two instruments, both keyed by ``(method, route, status)``:

- ``revue_http_requests_total`` — counter. Drives the error-rate alert
  (5xx ratio) and the traffic-anomaly alert (RPS vs baseline).
- ``revue_http_request_duration_seconds`` — histogram. Drives the latency
  alert (``histogram_quantile(0.95, ...)`` > 2s).

The ``route`` label is the FastAPI *route template* (e.g.
``/api/v2/licence/activate``), never the raw path, so per-request identifiers
cannot explode series cardinality.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple

# Histogram bucket upper bounds (seconds). The 2.0 boundary is mandatory: the
# latency alert pages on p95 > 2s, so the SLO edge must sit on a bucket
# boundary for histogram_quantile to resolve it without interpolation error.
# Buckets must be strictly ascending (Prometheus invariant).
LATENCY_BUCKETS_SECONDS: List[float] = [
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
]

_REQUESTS_METRIC = "revue_http_requests_total"
_DURATION_METRIC = "revue_http_request_duration_seconds"

# Label key, in stable emission order. Stable order keeps the rendered series
# identity deterministic (matters for the alert/dashboard PromQL string match).
_LABEL_KEYS = ("method", "route", "status")

_LabelTuple = Tuple[str, str, str]


def _escape_label_value(value: str) -> str:
    """Escape a label value per the Prometheus text exposition format.

    Order matters: backslash first, then quote and newline, otherwise the
    backslashes we introduce for ``"`` / ``\\n`` would be double-escaped.
    """
    return (
        value.replace("\\", r"\\")
        .replace('"', r"\"")
        .replace("\n", r"\n")
    )


def _format_float(value: float) -> str:
    """Render a float as a Prometheus-valid sample value.

    ``repr`` gives the shortest round-trippable decimal (``0.6`` not
    ``0.5999999999999``), and Prometheus accepts ``3.0`` and ``0.0`` as floats,
    so no special-casing of integers is needed."""
    return repr(value)


class MetricsRegistry:
    """Thread-safe in-process counter + histogram registry.

    A single instance is shared by the timing middleware across all requests.
    The web tier runs a single uvicorn worker (see Dockerfile), but uvicorn
    serves requests on a thread pool for sync work and the registry may be
    touched concurrently, so every mutation is guarded by one lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # series labels -> count
        self._counts: Dict[_LabelTuple, int] = {}
        # series labels -> per-bucket cumulative counts (parallel to buckets)
        self._buckets: Dict[_LabelTuple, List[int]] = {}
        # series labels -> (sum_seconds, count)
        self._sums: Dict[_LabelTuple, float] = {}

    def observe(self, *, method: str, route: str, status: int, duration_seconds: float) -> None:
        labels: _LabelTuple = (method, route, str(status))
        with self._lock:
            self._counts[labels] = self._counts.get(labels, 0) + 1

            bucket_counts = self._buckets.get(labels)
            if bucket_counts is None:
                bucket_counts = [0] * len(LATENCY_BUCKETS_SECONDS)
                self._buckets[labels] = bucket_counts
            # Cumulative histogram: increment every bucket whose upper bound is
            # >= the observed value.
            for i, upper in enumerate(LATENCY_BUCKETS_SECONDS):
                if duration_seconds <= upper:
                    bucket_counts[i] += 1

            self._sums[labels] = self._sums.get(labels, 0.0) + duration_seconds

    def render(self) -> str:
        """Render the full exposition text for all known series."""
        with self._lock:
            counts = dict(self._counts)
            buckets = {k: list(v) for k, v in self._buckets.items()}
            sums = dict(self._sums)

        lines: List[str] = []

        # ── Counter ──────────────────────────────────────────────────────
        lines.append(f"# HELP {_REQUESTS_METRIC} Total HTTP requests by method, route and status.")
        lines.append(f"# TYPE {_REQUESTS_METRIC} counter")
        for labels in sorted(counts):
            lines.append(f"{_REQUESTS_METRIC}{{{self._render_labels(labels)}}} {counts[labels]}")

        # ── Histogram ────────────────────────────────────────────────────
        lines.append(
            f"# HELP {_DURATION_METRIC} HTTP request latency in seconds by method, route and status."
        )
        lines.append(f"# TYPE {_DURATION_METRIC} histogram")
        for labels in sorted(buckets):
            bucket_counts = buckets[labels]
            base_labels = self._render_labels(labels)
            for i, upper in enumerate(LATENCY_BUCKETS_SECONDS):
                le = repr(upper)
                lines.append(
                    f'{_DURATION_METRIC}_bucket{{{base_labels},le="{le}"}} {bucket_counts[i]}'
                )
            total = counts.get(labels, bucket_counts[-1] if bucket_counts else 0)
            lines.append(
                f'{_DURATION_METRIC}_bucket{{{base_labels},le="+Inf"}} {total}'
            )
            lines.append(
                f"{_DURATION_METRIC}_sum{{{base_labels}}} {_format_float(sums.get(labels, 0.0))}"
            )
            lines.append(f"{_DURATION_METRIC}_count{{{base_labels}}} {total}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_labels(labels: _LabelTuple) -> str:
        return ",".join(
            f'{key}="{_escape_label_value(value)}"'
            for key, value in zip(_LABEL_KEYS, labels)
        )

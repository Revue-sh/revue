"""REVUE-362 — production observability primitives for the web tier.

Covers the dependency-free Prometheus exposition layer that powers the
``/activate`` error-rate / latency / traffic alerts and the dashboard:

- A thread-safe in-process ``MetricsRegistry`` that records one observation
  per HTTP request (method + route template + status code) and exposes a
  request counter plus a latency histogram.
- Prometheus text-exposition (v0.0.4) rendering, including ``# HELP`` /
  ``# TYPE`` headers, ``_bucket`` / ``_sum`` / ``_count`` series for the
  histogram, and a cumulative ``+Inf`` bucket.

The middleware/endpoint wiring is covered in ``test_metrics_endpoint.py``;
this file pins the registry contract in isolation so the alert PromQL
(which reads these exact series names) cannot silently drift.
"""
from __future__ import annotations

import threading

import pytest

from metrics import MetricsRegistry, LATENCY_BUCKETS_SECONDS


def test_observe_increments_request_counter_with_labels():
    registry = MetricsRegistry()

    registry.observe(method="POST", route="/api/v2/licence/activate", status=200, duration_seconds=0.1)

    text = registry.render()
    assert (
        'revue_http_requests_total{method="POST",route="/api/v2/licence/activate",status="200"} 1'
        in text
    )


def test_observe_accumulates_repeated_requests_on_same_series():
    registry = MetricsRegistry()

    for _ in range(3):
        registry.observe(method="POST", route="/api/v2/licence/activate", status=200, duration_seconds=0.05)

    text = registry.render()
    assert (
        'revue_http_requests_total{method="POST",route="/api/v2/licence/activate",status="200"} 3'
        in text
    )


def test_distinct_status_codes_are_separate_series():
    registry = MetricsRegistry()

    registry.observe(method="POST", route="/api/v2/licence/activate", status=200, duration_seconds=0.05)
    registry.observe(method="POST", route="/api/v2/licence/activate", status=500, duration_seconds=0.05)

    text = registry.render()
    assert 'status="200"} 1' in text
    assert 'status="500"} 1' in text


def test_render_emits_help_and_type_headers():
    registry = MetricsRegistry()
    registry.observe(method="GET", route="/health", status=200, duration_seconds=0.01)

    text = registry.render()
    assert "# HELP revue_http_requests_total" in text
    assert "# TYPE revue_http_requests_total counter" in text
    assert "# HELP revue_http_request_duration_seconds" in text
    assert "# TYPE revue_http_request_duration_seconds histogram" in text


def test_histogram_buckets_are_cumulative_and_include_inf():
    registry = MetricsRegistry()
    # 0.1s falls in the 0.25 bucket and every bucket above it.
    registry.observe(method="POST", route="/api/v2/licence/activate", status=200, duration_seconds=0.1)

    text = registry.render()
    # le="0.05" must NOT count it; le="0.25" and le="+Inf" must.
    assert (
        'revue_http_request_duration_seconds_bucket{method="POST",route="/api/v2/licence/activate",status="200",le="0.05"} 0'
        in text
    )
    assert (
        'revue_http_request_duration_seconds_bucket{method="POST",route="/api/v2/licence/activate",status="200",le="+Inf"} 1'
        in text
    )


def test_histogram_emits_sum_and_count():
    registry = MetricsRegistry()
    registry.observe(method="POST", route="/api/v2/licence/activate", status=200, duration_seconds=0.2)
    registry.observe(method="POST", route="/api/v2/licence/activate", status=200, duration_seconds=0.4)

    text = registry.render()
    assert (
        'revue_http_request_duration_seconds_count{method="POST",route="/api/v2/licence/activate",status="200"} 2'
        in text
    )
    # 0.2 + 0.4 sums to 0.6000000000000001 in IEEE-754; this prefix match
    # asserts the series is emitted and starts with the right magnitude, not an
    # exact decimal. Prometheus accepts the full float repr as a valid sample.
    assert (
        'revue_http_request_duration_seconds_sum{method="POST",route="/api/v2/licence/activate",status="200"} 0.6'
        in text
    )


def test_latency_buckets_span_the_two_second_slo():
    # The latency alert pages on p95 > 2s; a 2.0 boundary must exist so the
    # PromQL histogram_quantile resolves the SLO edge exactly.
    assert 2.0 in LATENCY_BUCKETS_SECONDS
    # Buckets must be strictly ascending for a valid Prometheus histogram.
    assert LATENCY_BUCKETS_SECONDS == sorted(LATENCY_BUCKETS_SECONDS)
    assert len(set(LATENCY_BUCKETS_SECONDS)) == len(LATENCY_BUCKETS_SECONDS)


def test_label_values_are_escaped_in_exposition():
    # A route or status that contains a quote / backslash / newline must be
    # escaped per the Prometheus text format, or the scrape breaks.
    registry = MetricsRegistry()
    registry.observe(method="GET", route='/weird"\\\npath', status=200, duration_seconds=0.01)

    text = registry.render()
    # The raw unescaped sequence must not appear; the escaped form must.
    assert '/weird"\\\npath' not in text
    assert r'/weird\"\\\npath' in text


def test_observe_is_thread_safe_under_concurrency():
    registry = MetricsRegistry()
    iterations = 500

    def worker():
        for _ in range(iterations):
            registry.observe(
                method="POST",
                route="/api/v2/licence/activate",
                status=200,
                duration_seconds=0.01,
            )

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = registry.render()
    expected = iterations * 4
    assert (
        f'revue_http_requests_total{{method="POST",route="/api/v2/licence/activate",status="200"}} {expected}'
        in text
    )

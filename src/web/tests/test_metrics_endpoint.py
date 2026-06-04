"""REVUE-362 — ``/metrics`` endpoint + request-timing middleware.

The middleware is endpoint-agnostic (Out of Scope: APM tracing internal to the
handler) — it times every request by route template + status and feeds the
shared registry. ``GET /metrics`` exposes the Prometheus text format that
Fly's managed Prometheus scrapes.

These are integration tests against the real ASGI app via the shared
``client`` fixture.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_metrics_endpoint_returns_prometheus_content_type(client):
    resp = await client.get("/metrics")

    assert resp.status_code == 200
    # Prometheus text exposition format v0.0.4.
    assert resp.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in resp.headers["content-type"]


async def test_request_is_counted_by_route_template_not_raw_path(client):
    # Hit a known handler, then read /metrics. The activate route should be
    # recorded under its TEMPLATE so high-cardinality raw paths never explode
    # the series count.
    await client.get("/health")

    resp = await client.get("/metrics")
    body = resp.text

    assert 'route="/health"' in body
    assert "revue_http_requests_total" in body


async def test_error_status_is_recorded_separately_from_success(client):
    # An unknown path yields 404 — the middleware must still record it so the
    # error-rate alert can see failures.
    await client.get("/definitely-not-a-real-route")

    resp = await client.get("/metrics")
    body = resp.text

    assert 'status="404"' in body


async def test_metrics_endpoint_is_not_self_counted(client):
    # Scraping /metrics must not inflate its own request counter, or the
    # traffic-anomaly baseline drifts every scrape interval.
    await client.get("/metrics")
    resp = await client.get("/metrics")
    body = resp.text

    assert 'route="/metrics"' not in body


async def test_prefixed_activate_route_is_recorded_under_its_template(client):
    # The whole alerting chain filters on route="/api/v2/licence/activate"; that
    # string must be the label the running app records for a request flowing
    # through include_router(prefix="/api"). A missing/bad content-type yields a
    # 400 but still routes to the activate handler, so the middleware records it.
    await client.post(
        "/api/v2/licence/activate",
        content=b"{}",
        headers={"content-type": "text/plain", "user-agent": "revue-cli/1.0"},
    )

    resp = await client.get("/metrics")
    body = resp.text

    # Exact template label the alert PromQL and dashboard depend on.
    assert 'route="/api/v2/licence/activate"' in body
    # And the raw-path-as-template anti-pattern is absent (no double /api prefix).
    assert 'route="/api/api/v2/licence/activate"' not in body


async def test_metrics_endpoint_resolves_on_the_api_subdomain(client):
    # On api.revue.sh the subdomain rewrite prepends /api to unprefixed paths.
    # /metrics must be exempt (passthrough) so the scrape endpoint resolves
    # identically on every host instead of 404-ing as /api/metrics.
    resp = await client.get("/metrics", headers={"host": "api.revue.sh"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


async def test_latency_histogram_is_populated_for_served_requests(client):
    await client.get("/health")

    resp = await client.get("/metrics")
    body = resp.text

    assert "revue_http_request_duration_seconds_bucket" in body
    assert 'route="/health"' in body
    assert "revue_http_request_duration_seconds_count" in body

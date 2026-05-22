"""Tests for the api.revue.sh subdomain path-rewrite middleware.

The middleware lets `api.revue.sh/<path>` reach the same handlers
currently mounted at `/api/<path>`, while leaving requests to the
marketing host (`revue.sh`) untouched so deployed CLI binaries
that hit `revue.sh/api/*` keep working.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_api_subdomain_rewrites_path_to_api_prefix(client) -> None:
    # POST /license/validate with empty body reaches the validator
    # and gets rejected with 422 (Pydantic validation error). 404
    # would mean the route is unmounted on this host.
    resp = await client.post(
        "/license/validate",
        json={},
        headers={"Host": "api.revue.sh"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_marketing_host_keeps_api_prefix(client) -> None:
    resp = await client.post(
        "/api/license/validate",
        json={},
        headers={"Host": "revue.sh"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_marketing_host_without_prefix_returns_404(client) -> None:
    resp = await client.post(
        "/license/validate",
        json={},
        headers={"Host": "revue.sh"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_subdomain_health_endpoint(client) -> None:
    resp = await client.get("/health", headers={"Host": "api.revue.sh"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_api_subdomain_does_not_double_prefix(client) -> None:
    # If a caller already includes /api on api.revue.sh, the middleware
    # must not rewrite to /api/api/... — the existing route still serves.
    resp = await client.post(
        "/api/license/validate",
        json={},
        headers={"Host": "api.revue.sh"},
    )
    assert resp.status_code == 422

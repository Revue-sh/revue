"""Tests for the /health endpoint."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_200(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

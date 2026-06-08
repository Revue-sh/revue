"""REVUE-127 — POST /usage/track endpoint tests (TC1–TC7).

Root-level path (/usage/track), key-based auth, JSON body, CSRF-exempt.
All tests will fail (RED) until T2–T4 are implemented.
"""
from __future__ import annotations

import json

import pytest
from database import REVIEWS_LIMIT_BY_TIER, get_db
from models import create_license_key, create_workspace, get_license_by_key


def _make_license(
    key: str,
    *,
    tier: str = "free",
    is_active: bool = True,
    reviews_used: int = 0,
) -> None:
    """Create a minimal user + workspace + license key in the test DB."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (f"{key}@test.com", "hashed"),
        )
        user_id = cur.lastrowid
        ws_id = create_workspace(conn, user_id, "test-ws")
        limit = REVIEWS_LIMIT_BY_TIER.get(tier)
        create_license_key(conn, ws_id, key=key, tier=tier, reviews_limit=limit)
        if not is_active:
            conn.execute("UPDATE license_keys SET is_active = 0 WHERE key = ?", (key,))
        if reviews_used:
            conn.execute(
                "UPDATE license_keys SET reviews_used_this_month = ? WHERE key = ?",
                (reviews_used, key),
            )


_BASE_PAYLOAD: dict = {
    "key": "test-key",
    "repo_id": "org/repo",
    "agents_used": ["orchestrator", "consolidator"],
    "duration_ms": 5000,
}


@pytest.mark.asyncio
async def test_track_free_tier_persists_and_increments(client):
    """TC1: free-tier key → 204, review_run persisted, reviews_used_this_month +1."""
    _make_license("free-key", tier="free")

    resp = await client.post("/usage/track", json={**_BASE_PAYLOAD, "key": "free-key"})
    assert resp.status_code == 204, resp.text

    with get_db() as conn:
        lic = get_license_by_key(conn, "free-key")
        runs = conn.execute(
            "SELECT * FROM review_runs WHERE license_key_id = ?", (lic.id,)
        ).fetchall()
    assert len(runs) == 1, "Expected one review_run persisted"
    assert lic.reviews_used_this_month == 1
    # AC3: persisted row must capture all event fields with server-side timestamp
    row = dict(runs[0])
    assert row["repo_id"] == "org/repo"
    assert json.loads(row["agents_used"]) == ["orchestrator", "consolidator"]
    assert row["duration_ms"] == 5000
    assert row["created_at"] is not None, "Server-side UTC timestamp must be set"


@pytest.mark.asyncio
async def test_track_pro_tier_persists_no_counter_increment(client):
    """TC2: pro-tier key → 204, review_run persisted, reviews_used_this_month unchanged."""
    _make_license("pro-key", tier="pro")

    resp = await client.post("/usage/track", json={**_BASE_PAYLOAD, "key": "pro-key"})
    assert resp.status_code == 204, resp.text

    with get_db() as conn:
        lic = get_license_by_key(conn, "pro-key")
        runs = conn.execute(
            "SELECT * FROM review_runs WHERE license_key_id = ?", (lic.id,)
        ).fetchall()
    assert len(runs) == 1, "Expected one review_run persisted"
    # Guard: pro tier must be unlimited (reviews_limit=None) for this assertion to be valid.
    assert REVIEWS_LIMIT_BY_TIER.get("pro") is None, "pro tier must have reviews_limit=None (unlimited)"
    assert lic.reviews_used_this_month == 0, "Unlimited tier must not increment counter"


@pytest.mark.asyncio
async def test_track_unknown_key_returns_401(client):
    """TC3: unknown key → 401."""
    resp = await client.post("/usage/track", json={**_BASE_PAYLOAD, "key": "does-not-exist"})
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_track_inactive_key_returns_403(client):
    """TC4: inactive key → 403."""
    _make_license("inactive-key", tier="free", is_active=False)

    resp = await client.post("/usage/track", json={**_BASE_PAYLOAD, "key": "inactive-key"})
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_track_idempotent_within_60s(client):
    """TC5: same key + repo_id within 60 s → second call returns 204 but no new run."""
    _make_license("idem-key", tier="free")
    payload = {**_BASE_PAYLOAD, "key": "idem-key", "repo_id": "org/dupe-repo"}

    r1 = await client.post("/usage/track", json=payload)
    assert r1.status_code == 204

    r2 = await client.post("/usage/track", json=payload)
    assert r2.status_code == 204

    with get_db() as conn:
        lic = get_license_by_key(conn, "idem-key")
        runs = conn.execute(
            "SELECT * FROM review_runs WHERE license_key_id = ?", (lic.id,)
        ).fetchall()
    assert len(runs) == 1, "Second call within 60 s must not create a duplicate run"
    assert lic.reviews_used_this_month == 1, "Counter incremented only once"


@pytest.mark.asyncio
async def test_track_missing_key_field_returns_400(client):
    """TC6: missing required field (key) → 400 with error body."""
    resp = await client.post(
        "/usage/track",
        json={"repo_id": "org/repo", "agents_used": [], "duration_ms": 1000},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "error" in body or "detail" in body or "message" in body


@pytest.mark.asyncio
async def test_track_exhausted_free_tier_still_persists_and_increments(client):
    """TC7: free-tier key at reviews_left=0 → 204, run persisted, counter increments.

    Floor-at-zero is enforced at READ time (max(0, limit - used)), not write time.
    The endpoint calls increment_usage unconditionally for metered tiers.
    """
    limit = REVIEWS_LIMIT_BY_TIER["free"]  # 25
    _make_license("exhausted-key", tier="free", reviews_used=limit)

    resp = await client.post("/usage/track", json={**_BASE_PAYLOAD, "key": "exhausted-key"})
    assert resp.status_code == 204, resp.text

    with get_db() as conn:
        lic = get_license_by_key(conn, "exhausted-key")
        runs = conn.execute(
            "SELECT * FROM review_runs WHERE license_key_id = ?", (lic.id,)
        ).fetchall()
    assert len(runs) == 1, "Run must be persisted even when reviews_left = 0"
    assert lic.reviews_used_this_month == limit + 1, "Counter increments beyond limit"


@pytest.mark.asyncio
async def test_track_non_json_body_returns_400(client):
    """Non-JSON body → 400 (the except-Exception → invalid_payload branch)."""
    resp = await client.post(
        "/usage/track",
        content=b"this is not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "error" in body or "message" in body


@pytest.mark.asyncio
async def test_track_idempotent_within_60s_null_repo_id(client):
    """TC5 variant: idempotency with repo_id omitted (normalised to NULL in DB)."""
    _make_license("null-repo-key", tier="free")
    payload = {**_BASE_PAYLOAD, "key": "null-repo-key", "repo_id": ""}

    r1 = await client.post("/usage/track", json=payload)
    assert r1.status_code == 204

    r2 = await client.post("/usage/track", json=payload)
    assert r2.status_code == 204

    with get_db() as conn:
        lic = get_license_by_key(conn, "null-repo-key")
        runs = conn.execute(
            "SELECT * FROM review_runs WHERE license_key_id = ?", (lic.id,)
        ).fetchall()
    assert len(runs) == 1, "Duplicate with NULL repo_id must not create a second run"
    assert lic.reviews_used_this_month == 1

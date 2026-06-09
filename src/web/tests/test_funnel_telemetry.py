"""REVUE-364 — POST /funnel/event and GET /funnel/weekly-conversion endpoint tests.

AC1: events emitted on install/activate/review
AC2: REVUE_TELEMETRY_OFF respected (backend: stores events; gating is CLI-side)
AC3: backend accepts funnel events; per-user funnel queryable
AC4: dashboard shows weekly install→activate→first-review conversion %
F1: raw licence key never stored — only SHA-256[:16] hash
F2: per-IP rate limit enforced
"""
from __future__ import annotations

import hashlib
import time

import pytest


_BASE_INSTALL_ID = "install-aabbccdd-1234"

_INSTALL_PAYLOAD: dict = {
    "event_type": "install",
    "install_id": _BASE_INSTALL_ID,
    "key": "",
    "ts": 0,
}


# ── POST /funnel/event ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_funnel_event_install_accepted(client):
    """AC1: install event → 204, persisted in funnel_events."""
    resp = await client.post("/funnel/event", json=_INSTALL_PAYLOAD)
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_funnel_event_activate_with_key(client):
    """AC1: activate event with key → 204."""
    payload = {**_INSTALL_PAYLOAD, "event_type": "activate", "key": "lic_testkey123"}
    resp = await client.post("/funnel/event", json=payload)
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_funnel_event_review_accepted(client):
    """AC1: review event → 204."""
    payload = {**_INSTALL_PAYLOAD, "event_type": "review"}
    resp = await client.post("/funnel/event", json=payload)
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_funnel_event_unknown_type_rejected(client):
    """Unknown event_type → 400."""
    payload = {**_INSTALL_PAYLOAD, "event_type": "unknown_type"}
    resp = await client.post("/funnel/event", json=payload)
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_funnel_event_install_id_required(client):
    """Missing install_id → 400."""
    payload = {"event_type": "install", "ts": 0}
    resp = await client.post("/funnel/event", json=payload)
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_funnel_event_non_json_body(client):
    """Non-JSON body → 400."""
    resp = await client.post(
        "/funnel/event",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_funnel_event_rate_limit(client):
    """Same install_id: 11+ events in 60s → 429 on the 11th."""
    payload = {**_INSTALL_PAYLOAD, "install_id": "ratelimit-id-0001"}
    # 10 accepted
    for _ in range(10):
        r = await client.post("/funnel/event", json=payload)
        assert r.status_code == 204, r.text
    # 11th is rate-limited
    r11 = await client.post("/funnel/event", json=payload)
    assert r11.status_code == 429, r11.text


@pytest.mark.asyncio
async def test_funnel_event_billing_path_unaffected(client, _tmp_db):
    """AC2/AC3 safety: funnel events must NOT touch reviews_used_this_month."""
    from database import get_db
    from models import create_license_key, create_workspace

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("funnel@test.com", "hashed"),
        )
        user_id = cur.lastrowid
        ws_id = create_workspace(conn, user_id, "ws")
        create_license_key(conn, ws_id, key="lic_funnel_safe", tier="free", reviews_limit=25)

    # Send a review funnel event with this key
    resp = await client.post(
        "/funnel/event",
        json={**_INSTALL_PAYLOAD, "event_type": "review", "key": "lic_funnel_safe"},
    )
    assert resp.status_code == 204

    # Billing counter must be untouched
    with get_db() as conn:
        row = conn.execute(
            "SELECT reviews_used_this_month FROM license_keys WHERE key = ?",
            ("lic_funnel_safe",),
        ).fetchone()
    assert row[0] == 0, "Funnel event must not increment billing counter"

    # F1: raw key must NOT be stored — only a hash
    with get_db() as conn:
        funnel_row = conn.execute(
            "SELECT license_key_hash FROM funnel_events WHERE install_id = ?",
            (_BASE_INSTALL_ID,),
        ).fetchone()
    assert funnel_row is not None
    assert funnel_row[0] != "lic_funnel_safe", "Raw key must not be stored"
    assert funnel_row[0] is not None, "Key hash must be stored"
    assert len(funnel_row[0]) == 16, "Hash must be 16-char truncated SHA-256"


# ── GET /funnel/weekly-conversion ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_funnel_weekly_conversion_unauthenticated(client):
    """GET /funnel/weekly-conversion without session → 401."""
    resp = await client.get("/funnel/weekly-conversion")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_funnel_weekly_conversion_returns_data(client):
    """AC4: authenticated request returns weekly funnel rows."""
    # Seed some events
    for iid, events in [
        ("iid-aaa", ["install", "activate", "review"]),
        ("iid-bbb", ["install", "activate"]),
        ("iid-ccc", ["install"]),
    ]:
        for ev in events:
            await client.post("/funnel/event", json={
                "event_type": ev,
                "install_id": iid,
                "key": "",
                "ts": int(time.time()),
            })

    # Sign up and establish session
    resp = await client.post(
        "/signup",
        data={"email": "admin@t.com", "password": "pw123456"},
        follow_redirects=False,
    )
    client.cookies.set("revue_session", resp.cookies.get("revue_session"))

    resp = await client.get("/funnel/weekly-conversion")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "weeks" in data
    weeks = data["weeks"]
    assert len(weeks) >= 1
    row = weeks[0]
    assert "install_week" in row
    assert "installs" in row
    assert "activates" in row
    assert "first_reviews" in row
    assert "install_to_activate_pct" in row
    assert "install_to_review_pct" in row
    # 3 installs, 2 activates, 1 review
    assert row["installs"] == 3
    assert row["activates"] == 2
    assert row["first_reviews"] == 1
    # Conversion %
    assert abs(row["install_to_activate_pct"] - 66.7) < 1.0
    assert abs(row["install_to_review_pct"] - 33.3) < 1.0

"""Tests for Story [66] — Basic analytics (finding trends by category and severity)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _signup(client: AsyncClient, email: str = "analytics@test.com") -> None:
    resp = await client.post(
        "/signup",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    client.cookies.set("revue_session", resp.cookies.get("revue_session"))


async def _get_key(email: str = "analytics@test.com") -> str:
    from database import get_db
    from models import get_user_by_email, get_license_for_user
    with get_db() as conn:
        user = get_user_by_email(conn, email)
        lic = get_license_for_user(conn, user.id)
        return lic.key if lic else ""


async def _seed(client: AsyncClient, runs: list[dict], email: str = "analytics@test.com") -> None:
    key = await _get_key(email)
    for r in runs:
        r.setdefault("key", key)
        await client.post("/api/usage/track", json=r)


# =====================================================================
# models.get_analytics unit tests
# =====================================================================

def test_get_analytics_empty(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "a@test.com", "h")
        create_workspace(conn, uid, "ws")
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    assert data["total_reviews"] == 0
    assert data["total_findings"] == 0
    assert data["reviews_over_time"] == []
    assert data["top_repos"] == []
    assert data["severity_totals"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_get_analytics_totals(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, create_review_run, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "b@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_b")
        create_review_run(conn, lic_id, repo_id="org/repo", findings_count=3,
                          findings_by_severity={"critical": 1, "high": 1, "medium": 1, "low": 0},
                          duration_ms=2000)
        create_review_run(conn, lic_id, repo_id="org/repo", findings_count=5,
                          findings_by_severity={"critical": 0, "high": 2, "medium": 2, "low": 1},
                          duration_ms=4000)
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    assert data["total_reviews"] == 2
    assert data["total_findings"] == 8
    assert data["avg_duration_ms"] == 3000


def test_get_analytics_severity_totals(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, create_review_run, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "c@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_c")
        create_review_run(conn, lic_id,
                          findings_by_severity={"critical": 2, "high": 3, "medium": 4, "low": 1})
        create_review_run(conn, lic_id,
                          findings_by_severity={"critical": 1, "high": 0, "medium": 2, "low": 3})
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    sev = data["severity_totals"]
    assert sev["critical"] == 3
    assert sev["high"] == 3
    assert sev["medium"] == 6
    assert sev["low"] == 4


def test_get_analytics_top_repos(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, create_review_run, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "d@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_d")
        # repo-a: 10 findings across 2 runs
        create_review_run(conn, lic_id, repo_id="org/repo-a", findings_count=6)
        create_review_run(conn, lic_id, repo_id="org/repo-a", findings_count=4)
        # repo-b: 2 findings
        create_review_run(conn, lic_id, repo_id="org/repo-b", findings_count=2)
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    repos = data["top_repos"]
    assert repos[0]["repo_id"] == "org/repo-a"
    assert repos[0]["findings"] == 10
    assert repos[0]["reviews"] == 2
    assert repos[1]["repo_id"] == "org/repo-b"


def test_get_analytics_top_repos_capped_at_5(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, create_review_run, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "e@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_e")
        for i in range(7):
            create_review_run(conn, lic_id, repo_id=f"org/repo-{i}", findings_count=i)
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    assert len(data["top_repos"]) <= 5


def test_get_analytics_status_breakdown(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, get_analytics
    import sqlite3
    with get_db() as conn:
        uid = create_user(conn, "f@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_f")
        # Insert directly to set specific statuses
        conn.execute("INSERT INTO review_runs (license_key_id, status) VALUES (?, 'completed')", (lic_id,))
        conn.execute("INSERT INTO review_runs (license_key_id, status) VALUES (?, 'completed')", (lic_id,))
        conn.execute("INSERT INTO review_runs (license_key_id, status) VALUES (?, 'failed')", (lic_id,))
        conn.execute("INSERT INTO review_runs (license_key_id, status) VALUES (?, 'skipped')", (lic_id,))
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    assert data["status_breakdown"]["completed"] == 2
    assert data["status_breakdown"]["failed"] == 1
    assert data["status_breakdown"]["skipped"] == 1


def test_get_analytics_reviews_over_time(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, create_review_run, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "g@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_g")
        create_review_run(conn, lic_id, findings_count=2)
        create_review_run(conn, lic_id, findings_count=3)
    with get_db() as conn:
        data = get_analytics(conn, uid, days=30)
    rot = data["reviews_over_time"]
    assert len(rot) >= 1
    # Today's entry should have count=2
    today_entry = rot[-1]
    assert today_entry["count"] == 2
    assert today_entry["findings"] == 5


def test_get_analytics_period_days_respected(_tmp_db):
    from database import get_db
    from models import create_user, create_workspace, create_license_key, get_analytics
    with get_db() as conn:
        uid = create_user(conn, "h@test.com", "h")
        wsid = create_workspace(conn, uid, "ws")
        lic_id = create_license_key(conn, wsid, "lic_h")
        # Insert a run with a date 60 days ago
        conn.execute(
            "INSERT INTO review_runs (license_key_id, findings_count, created_at) VALUES (?, 5, datetime('now', '-60 days'))",
            (lic_id,)
        )
        # Insert a recent run
        conn.execute(
            "INSERT INTO review_runs (license_key_id, findings_count) VALUES (?, 2)",
            (lic_id,)
        )
    with get_db() as conn:
        data_30 = get_analytics(conn, uid, days=30)
        data_90 = get_analytics(conn, uid, days=90)
    assert data_30["total_reviews"] == 1   # only recent
    assert data_90["total_reviews"] == 2   # both


# =====================================================================
# GET /api/analytics
# =====================================================================

@pytest.mark.asyncio
async def test_api_analytics_requires_auth(client: AsyncClient):
    resp = await client.get("/api/analytics")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_analytics_returns_empty(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/api/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_reviews"] == 0
    assert data["total_findings"] == 0
    assert "severity_totals" in data
    assert "top_repos" in data
    assert "reviews_over_time" in data
    assert "status_breakdown" in data


@pytest.mark.asyncio
async def test_api_analytics_shape(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/api/analytics")
    data = resp.json()
    assert "total_reviews" in data
    assert "total_findings" in data
    assert "avg_duration_ms" in data
    assert "period_days" in data
    assert data["period_days"] == 30


@pytest.mark.asyncio
async def test_api_analytics_days_param(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/api/analytics?days=7")
    data = resp.json()
    assert data["period_days"] == 7


@pytest.mark.asyncio
async def test_api_analytics_days_clamped(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/api/analytics?days=999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 365


@pytest.mark.asyncio
async def test_api_analytics_with_data(client: AsyncClient):
    await _signup(client)
    await _seed(client, [
        {"repo_id": "org/repo", "findings_count": 3,
         "findings_by_severity": {"critical": 1, "high": 1, "medium": 1, "low": 0},
         "duration_ms": 5000},
        {"repo_id": "org/repo", "findings_count": 2,
         "findings_by_severity": {"critical": 0, "high": 1, "medium": 1, "low": 0},
         "duration_ms": 3000},
    ])
    resp = await client.get("/api/analytics")
    data = resp.json()
    assert data["total_reviews"] == 2
    assert data["total_findings"] == 5
    assert data["avg_duration_ms"] == 4000
    assert data["severity_totals"]["critical"] == 1
    assert data["severity_totals"]["high"] == 2
    assert data["top_repos"][0]["repo_id"] == "org/repo"
    assert data["top_repos"][0]["reviews"] == 2


@pytest.mark.asyncio
async def test_api_analytics_findings_by_severity_stored(client: AsyncClient):
    await _signup(client)
    await _seed(client, [
        {"findings_count": 4,
         "findings_by_severity": {"critical": 2, "high": 1, "medium": 1, "low": 0}}
    ])
    resp = await client.get("/api/analytics")
    sev = resp.json()["severity_totals"]
    assert sev["critical"] == 2
    assert sev["high"] == 1


# =====================================================================
# GET /analytics page
# =====================================================================

@pytest.mark.asyncio
async def test_analytics_page_requires_auth(client: AsyncClient):
    resp = await client.get("/analytics", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_analytics_page_renders_empty(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/analytics")
    assert resp.status_code == 200
    assert b"Analytics" in resp.content
    assert b"No reviews in the last" in resp.content


@pytest.mark.asyncio
async def test_analytics_page_renders_with_data(client: AsyncClient):
    await _signup(client)
    await _seed(client, [
        {"repo_id": "org/myrepo", "findings_count": 5,
         "findings_by_severity": {"critical": 1, "high": 2, "medium": 2, "low": 0},
         "duration_ms": 8000},
    ])
    resp = await client.get("/analytics")
    assert resp.status_code == 200
    assert b"org/myrepo" in resp.content
    assert b"Top repositories" in resp.content
    assert b"Findings by severity" in resp.content
    assert b"Review status" in resp.content


@pytest.mark.asyncio
async def test_analytics_page_day_filter_buttons(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/analytics")
    assert b"7d" in resp.content
    assert b"30d" in resp.content
    assert b"90d" in resp.content


@pytest.mark.asyncio
async def test_analytics_page_days_param(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/analytics?days=7")
    assert b"Last 7 days" in resp.content


@pytest.mark.asyncio
async def test_dashboard_has_analytics_link(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/dashboard")
    assert b'href="/analytics"' in resp.content


# =====================================================================
# /api/runs now includes findings_by_severity
# =====================================================================

@pytest.mark.asyncio
async def test_api_runs_includes_findings_by_severity(client: AsyncClient):
    await _signup(client)
    await _seed(client, [
        {"findings_count": 3,
         "findings_by_severity": {"critical": 1, "high": 1, "medium": 1, "low": 0}}
    ])
    resp = await client.get("/api/runs")
    run = resp.json()["runs"][0]
    assert "findings_by_severity" in run
    assert run["findings_by_severity"]["critical"] == 1

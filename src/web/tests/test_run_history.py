"""Tests for Story [65] — Run history dashboard (/runs page + GET /api/runs)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _signup(client: AsyncClient, email: str = "runs@test.com") -> None:
    resp = await client.post(
        "/signup",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("revue_session")
    client.cookies.set("revue_session", cookie)


async def _get_license_key(email: str = "runs@test.com") -> str:
    """Get license key directly from DB for the given user."""
    from database import get_db
    from models import get_user_by_email, get_license_for_user
    with get_db() as conn:
        user = get_user_by_email(conn, email)
        if not user:
            return ""
        lic = get_license_for_user(conn, user.id)
        return lic.key if lic else ""


async def _seed_runs(client: AsyncClient, count: int = 3, key: str = "", email: str = "runs@test.com") -> str:
    """Track N review runs via the /api/usage/track endpoint. Returns the license key."""
    if not key:
        key = await _get_license_key(email)

    for i in range(count):
        await client.post("/api/usage/track", json={
            "key": key,
            "repo_id": f"workspace/repo-{i % 2}",
            "pr_title": f"feat: add feature {i}",
            "pr_number": 100 + i,
            "agents_used": ["orchestrator", "code-quality-expert"],
            "findings_count": i * 2,
            "duration_ms": 3000 + i * 500,
        })
    return key


# =====================================================================
# /runs page
# =====================================================================

@pytest.mark.asyncio
async def test_runs_page_requires_auth(client: AsyncClient):
    resp = await client.get("/runs", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_runs_page_renders_empty_state(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/runs")
    assert resp.status_code == 200
    assert b"Run History" in resp.content
    assert b"No review runs yet" in resp.content
    assert b"0 total review runs" in resp.content


@pytest.mark.asyncio
async def test_runs_page_shows_runs(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=3)
    resp = await client.get("/runs")
    assert resp.status_code == 200
    assert b"3 total review runs" in resp.content
    assert b"feat: add feature" in resp.content
    assert b"workspace/repo-" in resp.content


@pytest.mark.asyncio
async def test_runs_page_shows_findings_count(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=2)
    resp = await client.get("/runs")
    assert resp.status_code == 200
    # Run 0 has 0 findings (green), run 1 has 2 findings (yellow)
    assert b"text-green-400" in resp.content
    assert b"text-yellow-400" in resp.content


@pytest.mark.asyncio
async def test_runs_page_shows_pr_number(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=1)
    resp = await client.get("/runs")
    assert b"#100" in resp.content


@pytest.mark.asyncio
async def test_runs_page_filter_by_repo(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=4)  # creates repo-0 and repo-1 alternating
    resp = await client.get("/runs?repo=workspace/repo-0")
    assert resp.status_code == 200
    # Should only show repo-0 runs (2 of 4)
    assert b"workspace/repo-0" in resp.content
    assert b"2 total review runs" in resp.content


@pytest.mark.asyncio
async def test_runs_page_filter_by_status(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=3)
    resp = await client.get("/runs?status=completed")
    assert resp.status_code == 200
    assert b"3 total review runs" in resp.content


@pytest.mark.asyncio
async def test_runs_page_filter_no_results(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=2)
    resp = await client.get("/runs?repo=nonexistent/repo")
    assert resp.status_code == 200
    assert b"No runs match your filters" in resp.content


@pytest.mark.asyncio
async def test_runs_page_has_view_all_link_on_dashboard(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/dashboard")
    assert b'href="/runs"' in resp.content
    assert b"View all" in resp.content


@pytest.mark.asyncio
async def test_runs_page_has_back_to_dashboard_link(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/runs")
    assert b"Dashboard" in resp.content
    assert b"/dashboard" in resp.content


# =====================================================================
# GET /api/runs
# =====================================================================

@pytest.mark.asyncio
async def test_api_runs_requires_auth(client: AsyncClient):
    resp = await client.get("/api/runs")
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorised"


@pytest.mark.asyncio
async def test_api_runs_returns_empty(client: AsyncClient):
    await _signup(client)
    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["runs"] == []
    assert data["limit"] == 50
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_api_runs_returns_runs(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=3)
    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["runs"]) == 3


@pytest.mark.asyncio
async def test_api_runs_shape(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=1)
    resp = await client.get("/api/runs")
    run = resp.json()["runs"][0]
    assert "id" in run
    assert "repo_id" in run
    assert "pr_title" in run
    assert "pr_number" in run
    assert "agents_used" in run
    assert "findings_count" in run
    assert "duration_ms" in run
    assert "status" in run
    assert "created_at" in run


@pytest.mark.asyncio
async def test_api_runs_findings_count_stored(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=3)
    resp = await client.get("/api/runs")
    runs = resp.json()["runs"]
    # Runs are ordered newest first; findings_count = (index * 2) for seed
    findings = [r["findings_count"] for r in runs]
    # Should contain 0, 2, 4 in some order
    assert sorted(findings) == [0, 2, 4]


@pytest.mark.asyncio
async def test_api_runs_pagination(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=5)
    resp = await client.get("/api/runs?limit=2&offset=0")
    data = resp.json()
    assert data["total"] == 5
    assert len(data["runs"]) == 2

    resp2 = await client.get("/api/runs?limit=2&offset=2")
    data2 = resp2.json()
    assert len(data2["runs"]) == 2

    # IDs should be different pages
    ids_p1 = {r["id"] for r in data["runs"]}
    ids_p2 = {r["id"] for r in data2["runs"]}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_api_runs_filter_by_repo(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=4)
    resp = await client.get("/api/runs?repo_id=workspace/repo-0")
    data = resp.json()
    assert data["total"] == 2
    assert all(r["repo_id"] == "workspace/repo-0" for r in data["runs"])


@pytest.mark.asyncio
async def test_api_runs_pr_title_and_number_stored(client: AsyncClient):
    await _signup(client)
    await _seed_runs(client, count=1)
    resp = await client.get("/api/runs")
    run = resp.json()["runs"][0]
    assert run["pr_title"] == "feat: add feature 0"
    assert run["pr_number"] == 100


@pytest.mark.asyncio
async def test_api_runs_limit_cap(client: AsyncClient):
    """Limit is capped at 200 per page."""
    await _signup(client)
    resp = await client.get("/api/runs?limit=999")
    assert resp.status_code == 200  # no error, just capped internally


# =====================================================================
# /usage/track new fields
# =====================================================================

@pytest.mark.asyncio
async def test_track_accepts_new_fields(client: AsyncClient):
    await _signup(client)
    key = await _get_license_key()

    resp = await client.post("/api/usage/track", json={
        "key": key,
        "repo_id": "org/myrepo",
        "pr_title": "fix: resolve null pointer",
        "pr_number": 42,
        "agents_used": ["orchestrator", "sage"],
        "findings_count": 7,
        "duration_ms": 12000,
    })
    assert resp.status_code == 204

    runs_resp = await client.get("/api/runs")
    run = runs_resp.json()["runs"][0]
    assert run["pr_title"] == "fix: resolve null pointer"
    assert run["pr_number"] == 42
    assert run["findings_count"] == 7
    assert run["duration_ms"] == 12000

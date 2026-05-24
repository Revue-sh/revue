"""REVUE-278 Task 2 — ``POST /api/v2/usage/emit`` endpoint.

Covers AC6: the skill POSTs per-invocation usage records (reviews_run,
findings_count, ts from the client). The server verifies the supplied JWT,
derives ``workspace_id`` from the verified claims, validates the payload, and
writes one UsageEvent row.

Authentication is enforced via the same licence JWT issued by ``/activate``;
an unauthenticated caller cannot poison another tenant's usage counters.
"""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def _test_rsa_keypair() -> tuple[bytes, bytes]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


@pytest.fixture
def _patch_jwt_keys(monkeypatch, _test_rsa_keypair):
    priv_pem, pub_pem = _test_rsa_keypair
    monkeypatch.setenv("JWT_SIGNING_KEY", base64.b64encode(priv_pem).decode())
    import jwt_verify
    monkeypatch.setattr(jwt_verify, "JWT_PUBLIC_KEY_PEM", pub_pem.decode())
    return priv_pem, pub_pem


async def _create_active_workspace(client, *, email: str = "emit@test.com") -> int:
    """Sign up, create workspace + active licence_key, return workspace_id."""
    await client.post(
        "/signup",
        data={"email": email, "password": "password1"},
        follow_redirects=False,
    )
    from database import get_db
    from models import create_license_key, get_user_by_email

    with get_db() as conn:
        user = get_user_by_email(conn, email)
        cur = conn.execute(
            "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
            (user.id, "test-ws"),
        )
        workspace_id = cur.lastrowid  # type: ignore[assignment]
        # Cycle-2 M2: /validate + /usage/emit now enforce is_active. Every
        # active workspace in production has a licence_key — mirror that here.
        create_license_key(
            conn, workspace_id=workspace_id, key=f"test-key-{workspace_id}", tier="indie"
        )
        return workspace_id  # type: ignore[return-value]


def _sign_jwt_for(workspace_id: int, *, tier: str = "indie") -> str:
    from jwt_signing import sign_licence_jwt
    return sign_licence_jwt(
        workspace_id=workspace_id,
        tier=tier,
        machine_fingerprint="emit-test",
    )


@pytest.mark.asyncio
async def test_usage_emit_accepts_valid_payload(client, _patch_jwt_keys):
    """AC6: POST /api/v2/usage/emit with a valid JWT + payload persists one
    UsageEvent and returns 200."""
    wsid = await _create_active_workspace(client)
    token = _sign_jwt_for(wsid)

    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": token,
            "reviews_run": 2,
            "findings_count": 5,
            "ts": 1_750_000_000,
        },
    )

    assert resp.status_code == 200, resp.text

    from database import get_db
    from models import get_usage_events_for_workspace

    with get_db() as conn:
        events = get_usage_events_for_workspace(conn, wsid)
    assert len(events) == 1
    ev = events[0]
    assert ev.workspace_id == wsid
    assert ev.reviews_run == 2
    assert ev.findings_count == 5
    assert ev.emitted_at == 1_750_000_000


@pytest.mark.asyncio
async def test_usage_emit_rejects_unauthenticated_request(client):
    """No JWT → 422 (missing required field) or 401 — must NOT persist
    anything. Prevents cross-tenant telemetry forgery."""
    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "reviews_run": 1,
            "findings_count": 5,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code in (401, 422), resp.text


@pytest.mark.asyncio
async def test_usage_emit_rejects_invalid_jwt(client, _patch_jwt_keys):
    """Tampered/expired JWT → 401, no DB write."""
    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": "not.a.realjwt",
            "reviews_run": 1,
            "findings_count": 5,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "invalid_jwt"


@pytest.mark.asyncio
async def test_usage_emit_rejects_negative_counters(client, _patch_jwt_keys):
    """Counter values must be non-negative — prevents an attacker (or a
    bugged client) from subtracting from billing aggregates."""
    wsid = await _create_active_workspace(client)
    token = _sign_jwt_for(wsid)

    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": token,
            "reviews_run": -99,
            "findings_count": 5,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "invalid_counters"


@pytest.mark.asyncio
async def test_usage_emit_rejects_non_integer_reviews_run(client, _patch_jwt_keys):
    """Pydantic: reviews_run must be an int."""
    wsid = await _create_active_workspace(client)
    token = _sign_jwt_for(wsid)

    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": token,
            "reviews_run": "two",
            "findings_count": 5,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_usage_emit_uses_jwt_workspace_id_not_client_supplied(
    client, _patch_jwt_keys
):
    """The server must derive workspace_id from the JWT claims, NOT trust a
    client-supplied workspace_id field — otherwise attacker A could write
    events against tenant B's workspace by spoofing the body field."""
    wsid_a = await _create_active_workspace(client, email="a@test.com")
    wsid_b = await _create_active_workspace(client, email="b@test.com")
    token_a = _sign_jwt_for(wsid_a)

    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": token_a,
            "workspace_id": wsid_b,  # attacker-supplied; must be ignored
            "reviews_run": 1,
            "findings_count": 1,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code == 200

    from database import get_db
    from models import get_usage_events_for_workspace

    with get_db() as conn:
        events_a = get_usage_events_for_workspace(conn, wsid_a)
        events_b = get_usage_events_for_workspace(conn, wsid_b)
    # Event was recorded against workspace_id from JWT (a), not body (b)
    assert len(events_a) == 1
    assert len(events_b) == 0


@pytest.mark.asyncio
async def test_usage_emit_rejects_revoked_workspace(client, _patch_jwt_keys):
    """Cycle-2 M2: a revoked workspace must not be able to keep emitting
    telemetry until JWT exp. Symmetric with /v2/licence/validate."""
    wsid = await _create_active_workspace(client, email="emit-revoked@test.com")
    token = _sign_jwt_for(wsid)

    from database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE license_keys SET is_active = 0 WHERE workspace_id = ?", (wsid,),
        )
        conn.commit()

    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": token,
            "reviews_run": 1,
            "findings_count": 1,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "licence_revoked"


@pytest.mark.asyncio
async def test_usage_emit_rejects_oversized_jwt(client):
    """Cycle-2 M1: Pydantic max_length=4096 caps the jwt field on /usage/emit too.

    Symmetric defence with /licence/validate — both authenticated endpoints
    reject 1 MB JWT payloads at the schema layer before any decode work."""
    oversized = "x" * 4097
    resp = await client.post(
        "/api/v2/usage/emit",
        json={
            "jwt": oversized,
            "reviews_run": 1,
            "findings_count": 1,
            "ts": 1_750_000_000,
        },
    )
    assert resp.status_code == 422

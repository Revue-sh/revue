"""REVUE-278 Task 4 — ``POST /api/v2/licence/validate`` endpoint.

Covers AC1 (happy path: valid JWT), AC2–AC5 (error paths and tier symmetry).
The skill sends the JWT from ~/.config/revue/licence.jwt and receives
validation status, tier, reviews_remaining, refresh_after_ts, and optionally
a refreshed_jwt to write back.

Unlike the legacy /license/validate endpoint (which validates license keys
and tracks server-side usage), this endpoint:
- Accepts a JWT (not a license key)
- Verifies the JWT signature locally (no DB lookup needed for validity)
- Returns refresh_after_ts = issuance_ts + 86400 (server-issued)
- May return a refreshed_jwt for the client to write back
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def _test_rsa_keypair() -> tuple[bytes, bytes]:
    """Test RSA keypair. Never production."""
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
    """Patch JWT keys (server signing + server verification)."""
    priv_pem, pub_pem = _test_rsa_keypair
    monkeypatch.setenv("JWT_SIGNING_KEY", base64.b64encode(priv_pem).decode())
    import jwt_verify
    monkeypatch.setattr(jwt_verify, "JWT_PUBLIC_KEY_PEM", pub_pem.decode())
    return priv_pem, pub_pem


async def _create_active_workspace(client, *, email: str) -> int:
    """Sign up, insert workspace + active licence_key, return workspace_id.

    Required because /v2/licence/validate enforces is_active per cycle-2 M2 —
    a JWT for an unknown workspace_id is rejected as revoked."""
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
        create_license_key(
            conn, workspace_id=workspace_id, key=f"test-key-{workspace_id}", tier="indie"
        )
        return workspace_id  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_validate_happy_path_indie_tier(client, _patch_jwt_keys):
    """AC1: POST /api/v2/licence/validate with valid JWT returns the full
    response envelope: {valid, tier, reviews_remaining, refresh_after_ts,
    refreshed_jwt}."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    wsid = await _create_active_workspace(client, email="happy@test.com")
    now = datetime.now(timezone.utc)
    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="test-machine",
        now=now,
    )

    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["tier"] == "indie"
    assert "reviews_remaining" in body
    assert "refresh_after_ts" in body
    # refresh_after_ts is computed from the server's wall clock at
    # request-handling time (not from the JWT's issuance_ts claim — that
    # would let a leaked signing key mint tokens that never re-validate).
    expected_ts = int(now.timestamp()) + 86400
    assert abs(body["refresh_after_ts"] - expected_ts) < 5, (
        f"refresh_after_ts {body['refresh_after_ts']} not within 5s of "
        f"expected {expected_ts}"
    )


@pytest.mark.asyncio
async def test_validate_rejects_expired_jwt(client, _patch_jwt_keys):
    """AC1 / error: an expired JWT is rejected with valid: false."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    # Sign a token that's already expired (now = 2 days ago, expiry_days = 1)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    token = sign_licence_jwt(
        workspace_id=101,
        tier="pro",
        machine_fingerprint="expired-test",
        expiry_days=1,
        now=past,
    )

    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    # Assert
    assert resp.status_code == 200  # endpoint returns 200 even for invalid JWT
    body = resp.json()
    assert body["valid"] is False


@pytest.mark.asyncio
async def test_validate_rejects_tampered_jwt(client, _patch_jwt_keys):
    """AC1 / error: a JWT with a tampered signature is rejected."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    token = sign_licence_jwt(
        workspace_id=102,
        tier="free",
        machine_fingerprint="tamper-test",
    )
    # Tamper the signature
    parts = token.split(".")
    sig_bytes = base64.urlsafe_b64decode(parts[2] + "==")
    tampered_sig = bytes([sig_bytes[0] ^ 0xFF]) + sig_bytes[1:]
    tampered_token = (
        parts[0] + "." + parts[1] + "."
        + base64.urlsafe_b64encode(tampered_sig).decode().rstrip("=")
    )

    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": tampered_token},
    )

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False


@pytest.mark.asyncio
async def test_validate_free_tier_same_24h_window(client, _patch_jwt_keys):
    """AC5: Free and paid tiers get identical 24h cache window — no graded
    grace to prevent tier-bypass attacks."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    # Test both free and a paid tier
    for tier in ["free", "indie", "pro", "enterprise_starter"]:
        wsid = await _create_active_workspace(client, email=f"tier-{tier}@test.com")
        # Capture ``now`` per-iteration: each _create_active_workspace runs a
        # bcrypt signup (deliberately slow), so a single pre-loop ``now`` lets
        # cumulative hashing eat the 5s assertion budget by the 4th tier — a
        # pre-existing timing flake. Per-request horizon is what the AC means.
        now = datetime.now(timezone.utc)
        token = sign_licence_jwt(
            workspace_id=wsid,
            tier=tier,
            machine_fingerprint=f"tier-test-{tier}",
            now=now,
        )

        # Act
        resp = await client.post(
            "/api/v2/licence/validate",
            json={"jwt": token},
        )

        # Assert
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        # All tiers get the same 24h horizon (server clock + 86400).
        expected_ts = int(now.timestamp()) + 86400
        assert abs(body["refresh_after_ts"] - expected_ts) < 5, (
            f"tier {tier} got refresh_after_ts {body['refresh_after_ts']}, "
            f"expected ~{expected_ts}"
        )


@pytest.mark.asyncio
async def test_validate_returns_optional_refreshed_jwt(client, _patch_jwt_keys):
    """AC1 decision #5: when server issues a refreshed JWT, it's returned
    in the response so the client can overwrite ~/.config/revue/licence.jwt."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    wsid = await _create_active_workspace(client, email="refresh@test.com")
    now = datetime.now(timezone.utc)
    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="refresh-test",
        now=now,
    )

    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    # refreshed_jwt may or may not be present (server decides rotation policy).
    # If present, it must be a valid JWT.
    if "refreshed_jwt" in body and body["refreshed_jwt"]:
        from jwt_verify import decode_licence_jwt
        refreshed_claims = decode_licence_jwt(body["refreshed_jwt"])
        assert refreshed_claims["workspace_id"] == wsid
        assert refreshed_claims["tier"] == "indie"


@pytest.mark.asyncio
async def test_validate_rejects_malformed_json(client):
    """Schema validation: malformed JSON returns 422."""
    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": ""},  # empty JWT
    )

    # Assert — empty JWT fails decode (not valid base64)
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False


@pytest.mark.asyncio
async def test_validate_missing_jwt_field(client):
    """Schema validation: missing jwt field returns 422."""
    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={},
    )

    # Assert
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_validate_rejects_oversized_jwt(client):
    """Cycle-2 M1: Pydantic max_length=4096 caps the jwt field.

    Real RS256 licence JWTs are under 2 KB; an attacker POSTing a 1 MB
    string would otherwise burn server CPU on the failing decode. Match
    the asymmetric-attack-surface defence ActivateRequest already applies
    to machine_fingerprint."""
    oversized = "x" * 4097
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": oversized},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_validate_rejects_revoked_workspace(client, _patch_jwt_keys):
    """Cycle-2 M2: a cryptographically valid JWT for a workspace whose
    licence has been deactivated must be rejected as valid:false.

    Without this gate, the only way to revoke a leaked/churned JWT is full
    signing-key rotation (which nukes every customer). The DB lookup
    bounds revocation lag at the 24h cache window already accepted by
    PM-plan decision #4."""
    from jwt_signing import sign_licence_jwt

    wsid = await _create_active_workspace(client, email="revoked@test.com")

    # Deactivate every licence_key for this workspace.
    from database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE license_keys SET is_active = 0 WHERE workspace_id = ?", (wsid,),
        )
        conn.commit()

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="revoked-test",
    )

    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False, (
        "revoked workspace must not validate even with a fresh JWT"
    )


# --- REVUE-279 Task 2: paywall_state in /validate response ---


@pytest.mark.asyncio
async def test_validate_paywall_state_returns_none_free_tier_under_cap(
    client, _patch_jwt_keys
):
    """REVUE-279 AC1: free-tier workspace with < 25 events this month
    returns paywall_state: None."""
    from jwt_signing import sign_licence_jwt
    from database import get_db
    from models import create_license_key, get_user_by_email, record_usage_event

    # Arrange — create free-tier workspace with 10 usage events
    await client.post(
        "/signup",
        data={"email": "free-under@test.com", "password": "password1"},
        follow_redirects=False,
    )
    with get_db() as conn:
        user = get_user_by_email(conn, "free-under@test.com")
        cur = conn.execute(
            "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
            (user.id, "free-ws"),
        )
        wsid = cur.lastrowid  # type: ignore[assignment]
        create_license_key(
            conn, workspace_id=wsid, key=f"free-key-{wsid}", tier="free"
        )
        # Insert 10 usage events
        for _ in range(10):
            record_usage_event(
                conn,
                workspace_id=wsid,
                reviews_run=1,
                findings_count=0,
                emitted_at=0,
            )

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="free",
        machine_fingerprint="free-under-test",
    )

    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["tier"] == "free"
    assert body["reviews_remaining"] == 15  # 25 - 10
    assert "paywall_state" in body
    assert body["paywall_state"] is None


@pytest.mark.asyncio
async def test_validate_paywall_state_returns_exhausted_free_tier_at_cap(
    client, _patch_jwt_keys
):
    """REVUE-279 AC1: free-tier workspace with >= 25 events this month
    returns paywall_state: "exhausted"."""
    from jwt_signing import sign_licence_jwt
    from database import get_db
    from models import create_license_key, get_user_by_email, record_usage_event

    # Arrange — create free-tier workspace with 25 usage events
    await client.post(
        "/signup",
        data={"email": "free-exhausted@test.com", "password": "password1"},
        follow_redirects=False,
    )
    with get_db() as conn:
        user = get_user_by_email(conn, "free-exhausted@test.com")
        cur = conn.execute(
            "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
            (user.id, "free-ws-exhausted"),
        )
        wsid = cur.lastrowid  # type: ignore[assignment]
        create_license_key(
            conn, workspace_id=wsid, key=f"free-key-{wsid}", tier="free"
        )
        # Insert 25 usage events (at the cap)
        for _ in range(25):
            record_usage_event(
                conn,
                workspace_id=wsid,
                reviews_run=1,
                findings_count=0,
                emitted_at=0,
            )

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="free",
        machine_fingerprint="free-exhausted-test",
    )

    # Act
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["tier"] == "free"
    assert body["reviews_remaining"] == 0
    assert body["paywall_state"] == "exhausted"


@pytest.mark.asyncio
async def test_validate_paywall_state_always_none_for_paid_tiers(
    client, _patch_jwt_keys
):
    """REVUE-279 AC3: paid tiers always return paywall_state: None,
    even if their workspace had >= 25 usage events (they have no cap)."""
    from jwt_signing import sign_licence_jwt
    from database import get_db
    from models import create_license_key, get_user_by_email, record_usage_event

    # Arrange — for each paid tier, insert 30 usage events and validate
    for tier in ["indie", "pro", "enterprise_starter"]:
        await client.post(
            "/signup",
            data={"email": f"paid-{tier}@test.com", "password": "password1"},
            follow_redirects=False,
        )
        with get_db() as conn:
            user = get_user_by_email(conn, f"paid-{tier}@test.com")
            cur = conn.execute(
                "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
                (user.id, f"paid-ws-{tier}"),
            )
            wsid = cur.lastrowid  # type: ignore[assignment]
            create_license_key(
                conn, workspace_id=wsid, key=f"paid-key-{wsid}", tier=tier
            )
            # Insert 30 usage events (well above the free-tier cap)
            for _ in range(30):
                record_usage_event(
                    conn,
                    workspace_id=wsid,
                    reviews_run=1,
                    findings_count=0,
                    emitted_at=0,
                )

        token = sign_licence_jwt(
            workspace_id=wsid,
            tier=tier,
            machine_fingerprint=f"paid-{tier}-test",
        )

        # Act
        resp = await client.post(
            "/api/v2/licence/validate",
            json={"jwt": token},
        )

        # Assert
        assert resp.status_code == 200, f"tier {tier} validation failed"
        body = resp.json()
        assert body["valid"] is True
        assert body["tier"] == tier
        assert body["reviews_remaining"] is None, (
            f"paid tier {tier} should have reviews_remaining=None"
        )
        assert "paywall_state" in body
        assert body["paywall_state"] is None, (
            f"paid tier {tier} should have paywall_state=None even with 30 events"
        )


# --- REVUE-279 code-review Fix 7: response shape uniform on valid:false ---


@pytest.mark.asyncio
async def test_validate_invalid_jwt_response_includes_paywall_state(client):
    """Fix 7: a JWT that fails decode (signature, claims, expiry, malformed)
    returns ``valid: false`` — the response shape must include
    ``paywall_state: None`` so clients can rely on uniform keys regardless
    of valid:true/false. Pre-fix the key was absent."""
    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": "not.a.real.jwt"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "paywall_state" in body, (
        "valid:false response must include paywall_state key for shape parity"
    )
    assert body["paywall_state"] is None


@pytest.mark.asyncio
async def test_validate_revoked_workspace_response_includes_paywall_state(
    client, _patch_jwt_keys
):
    """Fix 7: revocation path (cryptographically valid JWT but workspace's
    licence deactivated) returns valid:false — must also carry
    ``paywall_state: None`` for shape uniformity."""
    from jwt_signing import sign_licence_jwt

    wsid = await _create_active_workspace(client, email="revoked-shape@test.com")

    from database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE license_keys SET is_active = 0 WHERE workspace_id = ?",
            (wsid,),
        )
        conn.commit()

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="revoked-shape-test",
    )

    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "paywall_state" in body
    assert body["paywall_state"] is None


# --- REVUE-279 code-review Fix 8: cap sourced from REVIEWS_LIMIT_BY_TIER ---


@pytest.mark.asyncio
async def test_validate_uses_reviews_limit_by_tier_for_free_cap(
    client, _patch_jwt_keys, monkeypatch
):
    """Fix 8: the free-tier cap must be read from
    ``database.REVIEWS_LIMIT_BY_TIER["free"]``, not a hardcoded literal.
    Patch the constant and confirm ``reviews_remaining`` shifts."""
    from jwt_signing import sign_licence_jwt
    from database import REVIEWS_LIMIT_BY_TIER, get_db
    from models import (
        create_license_key,
        get_user_by_email,
        record_usage_event,
    )

    # Bump the free-tier cap to 50 to prove the response tracks the constant.
    patched = dict(REVIEWS_LIMIT_BY_TIER)
    patched["free"] = 50
    import database as database_module
    monkeypatch.setattr(database_module, "REVIEWS_LIMIT_BY_TIER", patched)
    # The route imports the constant at module-load time, so patch the
    # binding inside api_routes too.
    import routes.api_routes as api_routes_module
    monkeypatch.setattr(
        api_routes_module, "REVIEWS_LIMIT_BY_TIER", patched
    )

    await client.post(
        "/signup",
        data={"email": "fix8-cap@test.com", "password": "password1"},
        follow_redirects=False,
    )
    with get_db() as conn:
        user = get_user_by_email(conn, "fix8-cap@test.com")
        cur = conn.execute(
            "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
            (user.id, "fix8-ws"),
        )
        wsid = cur.lastrowid  # type: ignore[assignment]
        create_license_key(
            conn, workspace_id=wsid, key=f"fix8-key-{wsid}", tier="free"
        )
        for _ in range(10):
            record_usage_event(
                conn,
                workspace_id=wsid,
                reviews_run=1,
                findings_count=0,
                emitted_at=0,
            )

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="free",
        machine_fingerprint="fix8-test",
    )

    resp = await client.post(
        "/api/v2/licence/validate",
        json={"jwt": token},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    # With the patched cap (50) and 10 usage events, reviews_remaining = 40.
    # If Fix 8 was reverted and the cap was hardcoded to 25, this would
    # return 15 (25 - 10) instead.
    assert body["reviews_remaining"] == 40, (
        f"expected cap from REVIEWS_LIMIT_BY_TIER (50) - 10 events = 40, "
        f"got {body['reviews_remaining']} — Fix 8 may have regressed to "
        f"a hardcoded literal"
    )
    assert body["paywall_state"] is None


# ---------------------------------------------------------------------------
# REVUE-382 — successful validation persists last_validated_at
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_persists_last_validated_at(client, _patch_jwt_keys):
    """REVUE-382 AC2 source: a SUCCESSFUL validation stamps last_validated_at
    on the workspace's licence row (UTC, naive-isoformat to match the other
    timestamp columns). This is the server-side validation-cache state the
    Account → Plan page renders as 'Last verified Nh ago'."""
    from datetime import datetime, timezone
    from jwt_signing import sign_licence_jwt
    from database import get_db
    from models import get_active_license_for_workspace

    wsid = await _create_active_workspace(client, email="lastval@test.com")

    # Pre-condition: never validated yet.
    with get_db() as conn:
        before = get_active_license_for_workspace(conn, wsid)
    assert before is not None
    assert before.last_validated_at is None

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="lastval-machine",
        now=datetime.now(timezone.utc),
    )

    resp = await client.post("/api/v2/licence/validate", json={"jwt": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["valid"] is True

    # Post-condition: last_validated_at is now set and parseable as a recent
    # naive-UTC isoformat timestamp.
    with get_db() as conn:
        after = get_active_license_for_workspace(conn, wsid)
    assert after is not None
    assert after.last_validated_at is not None
    stamped = datetime.fromisoformat(after.last_validated_at)
    # Naive (no tzinfo) to match period_reset_at representation.
    assert stamped.tzinfo is None
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now_naive - stamped).total_seconds()) < 30


@pytest.mark.asyncio
async def test_validate_failure_does_not_stamp_last_validated_at(client, _patch_jwt_keys):
    """A FAILED validation (revoked / unknown workspace) must NOT stamp
    last_validated_at — only successful validations update the cache state."""
    from datetime import datetime, timezone
    from jwt_signing import sign_licence_jwt
    from database import get_db
    from models import get_active_license_for_workspace

    wsid = await _create_active_workspace(client, email="nostamp@test.com")

    # Revoke the licence so validation fails the is_active gate.
    with get_db() as conn:
        conn.execute(
            "UPDATE license_keys SET is_active = 0 WHERE workspace_id = ?", (wsid,)
        )

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="nostamp-machine",
        now=datetime.now(timezone.utc),
    )
    resp = await client.post("/api/v2/licence/validate", json={"jwt": token})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False

    # The (now-inactive) row must still have NULL last_validated_at. Read it
    # back directly since get_active_license_for_workspace filters is_active.
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_validated_at FROM license_keys WHERE workspace_id = ?",
            (wsid,),
        ).fetchone()
    assert row["last_validated_at"] is None


@pytest.mark.asyncio
async def test_validate_stamp_failure_still_returns_valid(client, _patch_jwt_keys, monkeypatch):
    """REVUE-382 availability: the last_validated_at stamp is a NON-critical
    cache write. If it raises (transient DB lock/disk), the CRITICAL validate
    path must still return 200 / valid:True — the cache write must never break
    the CLI's licence check."""
    from datetime import datetime, timezone
    from jwt_signing import sign_licence_jwt
    import routes.api_routes as api_routes

    wsid = await _create_active_workspace(client, email="stampfail@test.com")

    # Force the best-effort stamp to raise a transient-style DB error.
    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_routes, "touch_license_validated", _boom)

    token = sign_licence_jwt(
        workspace_id=wsid,
        tier="indie",
        machine_fingerprint="stampfail-machine",
        now=datetime.now(timezone.utc),
    )

    resp = await client.post("/api/v2/licence/validate", json={"jwt": token})

    # The stamp failed, but validation must still succeed.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["tier"] == "indie"
    assert "refresh_after_ts" in body

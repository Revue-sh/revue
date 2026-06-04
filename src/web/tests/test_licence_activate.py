"""REVUE-277 Phase 2 — POST /api/v2/licence/activate.

Covers AC1 (success: signed JWT with full claim set), AC4 (error paths:
invalid key, inactive licence, missing Fly secret), and TC3/TC4
(actionable error envelopes).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# ---------------------------------------------------------------------------
# Test-only RSA keypair — generated fresh per session, never the production
# key. Production verification uses the constant baked into revue_core; tests
# patch that constant so the JWT round-trips against a key we control here.
# ---------------------------------------------------------------------------


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
    """Point the signing side at the test keypair and hand the public half back.

    The backend signs with ``JWT_SIGNING_KEY`` (read from env at sign time), and
    each test decodes the issued token against the returned ``pub_pem`` directly
    — so only the signing half needs patching here. The web app inlines its own
    public-key constant (REVUE-345: the web container ships without revue_core),
    so there is nothing in revue_core to patch on the verification side.
    """
    priv_pem, pub_pem = _test_rsa_keypair
    # Backend reads JWT_SIGNING_KEY from env at sign time (base64-encoded PEM)
    monkeypatch.setenv("JWT_SIGNING_KEY", base64.b64encode(priv_pem).decode())
    return priv_pem, pub_pem


# ---------------------------------------------------------------------------
# Database helper — insert a workspace + active licence for the test user
# ---------------------------------------------------------------------------


async def _create_active_licence(
    client, *, tier: str = "indie", is_active: bool = True
) -> str:
    """Sign up a user, create a workspace + active licence, return the key."""
    # Reuse the existing signup helper pattern
    await client.post(
        "/signup",
        data={"email": "activate@test.com", "password": "password1"},
        follow_redirects=False,
    )
    # Insert workspace + licence directly via the DB so we don't depend on the
    # Stripe webhook plumbing for this test
    from database import get_db
    from license import generate_license_key

    key = generate_license_key()
    with get_db() as conn:
        from models import get_user_by_email
        user = get_user_by_email(conn, "activate@test.com")
        cur = conn.execute(
            "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
            (user.id, "test-ws"),
        )
        ws_id = cur.lastrowid
        conn.execute(
            "INSERT INTO license_keys (workspace_id, key, tier, is_active, "
            "reviews_used_this_month, reviews_limit, period_reset_at) "
            "VALUES (?, ?, ?, ?, 0, 100, ?)",
            (ws_id, key, tier, 1 if is_active else 0,
             (datetime.utcnow() + timedelta(days=30)).isoformat()),
        )
    return key


# ---------------------------------------------------------------------------
# AC1 — Success: signed JWT with full claim set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_returns_jwt_with_required_claims(
    client, _patch_jwt_keys
):
    """AC1: POST /api/v2/licence/activate returns an RS256-signed JWT whose
    claims include workspace_id, tier, issuance_ts, expiry_ts, and
    machine_fingerprint."""
    key = await _create_active_licence(client, tier="indie")

    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "abc-123-fp"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "jwt" in body
    assert body["tier"] == "indie"

    # The JWT must verify against the (patched) public key
    _, pub_pem = _patch_jwt_keys
    claims = pyjwt.decode(body["jwt"], pub_pem.decode(), algorithms=["RS256"])

    assert "workspace_id" in claims and isinstance(claims["workspace_id"], int)
    assert claims["tier"] == "indie"
    assert isinstance(claims["issuance_ts"], int)
    # S1: standard PyJWT claim name so `verify_exp` enforces expiry.
    assert isinstance(claims["exp"], int)
    assert claims["exp"] > claims["issuance_ts"]
    assert claims["machine_fingerprint"] == "abc-123-fp"


@pytest.mark.asyncio
async def test_activate_jwt_header_pins_rs256(client, _patch_jwt_keys):
    """AC1 (defence in depth): the JWT header must declare RS256. Anything
    else — e.g. ``none`` — would be a critical signing-config regression."""
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "fp"},
    )
    header = pyjwt.get_unverified_header(resp.json()["jwt"])
    assert header["alg"] == "RS256"


# ---------------------------------------------------------------------------
# AC4 — Error paths: invalid key, inactive licence, server misconfiguration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_rejects_unknown_key(client, _patch_jwt_keys):
    """AC4 / TC3: an unknown key returns 404 with an actionable error code
    and message. No JWT is issued."""
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": "lic_does_not_exist", "machine_fingerprint": "fp"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "invalid_key"
    assert "not recognised" in body["message"].lower() \
        or "not recognized" in body["message"].lower()
    assert "jwt" not in body


@pytest.mark.asyncio
async def test_activate_rejects_inactive_licence(client, _patch_jwt_keys):
    """AC4: a deactivated licence (cancelled subscription, fraud hold)
    returns 403 with an actionable error code; no JWT is issued."""
    key = await _create_active_licence(client, is_active=False)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "fp"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "inactive_licence"
    assert "support@revue.sh" in body["message"]
    assert "jwt" not in body


@pytest.mark.asyncio
async def test_activate_rejects_missing_signing_key_env(client, monkeypatch):
    """AC4: if the Fly secret JWT_SIGNING_KEY is unset (operator error), the
    endpoint returns 500 with a server_misconfigured error and a pointer to
    the runbook. The licence row is unaffected — never silently issue an
    unsigned token."""
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "fp"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "server_misconfigured"
    assert "JWT_SIGNING_KEY" in body["message"]
    assert "jwt" not in body


# ---------------------------------------------------------------------------
# TC4 — Browser /activate flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_activate_get_renders_form(client):
    """The browser activation page must render a form (HTML) at GET
    /activate — no auth required, anyone with a key can land here."""
    resp = await client.get("/activate")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text
    # The form must include a key input + submit
    assert "licence key" in html.lower() or "license key" in html.lower()
    assert "<form" in html.lower()
    assert "type=\"submit\"" in html.lower() or "type='submit'" in html.lower()

    # A1: post-REVUE-275 the binary is named ``revue``, not ``revue-local``.
    # The user-visible copy on this page must NOT instruct the user to run
    # ``revue-local activate <key>`` — that command no longer exists. The
    # CLI fallback must point at the current binary name.
    assert "revue activate" in html, (
        "activate.html must mention the current ``revue activate`` CLI command"
    )
    assert "revue-local activate" not in html, (
        "activate.html references stale binary name ``revue-local activate``; "
        "the binary was renamed to ``revue`` in REVUE-275"
    )


# ---------------------------------------------------------------------------
# Validation: machine_fingerprint missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_rejects_missing_machine_fingerprint(
    client, _patch_jwt_keys
):
    """Schema enforcement: machine_fingerprint is a required field. Omitting
    it returns 422 (pydantic validation) — never silently sign a token with
    an empty fingerprint."""
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key},  # missing machine_fingerprint
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# S5 — invalid base64 in JWT_SIGNING_KEY surfaces an actionable 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_rejects_malformed_base64_signing_key(
    client, monkeypatch
):
    """S5: when JWT_SIGNING_KEY is set but not valid base64 (operator
    misconfig — e.g. raw PEM pasted without encoding), the endpoint must
    return 500 with a clear hint pointing at the encoding problem, not
    a cryptic stack trace later in the encode call."""
    monkeypatch.setenv("JWT_SIGNING_KEY", "this is not valid base64 @@@!!!")
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "fp"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "server_misconfigured"
    assert "base64" in body["message"].lower()
    assert "jwt" not in body


# ---------------------------------------------------------------------------
# S9 — machine_fingerprint length + charset validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_rejects_oversized_fingerprint(client, _patch_jwt_keys):
    """S9: a 1 MB fingerprint string must be rejected with 422
    invalid_fingerprint — the server must not blindly sign whatever the
    client supplies."""
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "a" * (1024 * 1024)},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("error") == "invalid_fingerprint"


@pytest.mark.asyncio
async def test_activate_rejects_empty_fingerprint(client, _patch_jwt_keys):
    """S9: empty fingerprint string fails validation."""
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": ""},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("error") == "invalid_fingerprint"


@pytest.mark.asyncio
async def test_activate_rejects_non_alphanumeric_fingerprint(
    client, _patch_jwt_keys
):
    """S9: fingerprint must match [a-zA-Z0-9_-]+. Anything else is
    rejected — defends against injection of control characters or
    payloads embedded in claims."""
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "abc; rm -rf /"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("error") == "invalid_fingerprint"


@pytest.mark.asyncio
async def test_activate_rejects_fingerprint_with_trailing_newline(
    client, _patch_jwt_keys
):
    """S9 regression: ``re.match`` against a pattern anchored with ``^``
    and ``$`` will happily accept ``"abc\n"`` because default-flag ``$``
    matches end-of-string OR just before a trailing newline. That
    behaviour would sign the ``\n`` byte into the JWT ``machine_fingerprint``
    claim — defeating the charset whitelist. The endpoint must use
    ``fullmatch`` (or anchor with ``\\Z``) so a trailing newline is rejected
    like any other disallowed character.
    """
    key = await _create_active_licence(client)
    resp = await client.post(
        "/api/v2/licence/activate",
        json={"key": key, "machine_fingerprint": "abc\n"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("error") == "invalid_fingerprint"

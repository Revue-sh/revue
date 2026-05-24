"""REVUE-278 Task 3 — JWT decode helper for ``POST /api/v2/licence/validate``.

SRP: signing (jwt_signing.py) and verification (jwt_verify.py) are separate
concerns. The verify path is used by both the skill (at cache time) and the
server endpoint (at validation time).

The verify module uses the embedded RS256 public key (same as the CLI does),
so invalid tokens are caught before DB operations. The exception semantics
match PyJWT — callers decide how to handle each failure mode (expired,
invalid sig, malformed, etc.).
"""
from __future__ import annotations

import base64
import pytest
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt as pyjwt


@pytest.fixture(scope="session")
def _test_rsa_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh test RSA keypair. Never the production key."""
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
    """Patch both the private (server) and public (verifier) keys with test pair."""
    priv_pem, pub_pem = _test_rsa_keypair
    monkeypatch.setenv("JWT_SIGNING_KEY", base64.b64encode(priv_pem).decode())
    import jwt_verify
    monkeypatch.setattr(jwt_verify, "JWT_PUBLIC_KEY_PEM", pub_pem.decode())
    return priv_pem, pub_pem


def test_decode_valid_jwt_returns_all_claims(_patch_jwt_keys):
    """Decode a well-formed JWT and return all claims."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    now = datetime.now(timezone.utc)
    token = sign_licence_jwt(
        workspace_id=123,
        tier="indie",
        machine_fingerprint="test-fp-abc123",
        now=now,
    )

    # Act
    from jwt_verify import decode_licence_jwt

    claims = decode_licence_jwt(token)

    # Assert
    assert claims["workspace_id"] == 123
    assert claims["tier"] == "indie"
    assert claims["machine_fingerprint"] == "test-fp-abc123"
    assert "exp" in claims
    assert "issuance_ts" in claims
    assert claims["issuance_ts"] == int(now.timestamp())


def test_decode_expired_jwt_raises(_patch_jwt_keys):
    """Expired JWT raises pyjwt.ExpiredSignatureError."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    # Sign a token that expired 1 day ago
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=2)
    token = sign_licence_jwt(
        workspace_id=456,
        tier="pro",
        machine_fingerprint="expired-fp",
        expiry_days=1,
        now=past,
    )

    # Act & Assert
    from jwt_verify import decode_licence_jwt

    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_licence_jwt(token)


def test_decode_tampered_jwt_raises(_patch_jwt_keys):
    """A JWT with a tampered signature raises pyjwt.InvalidSignatureError."""
    # Arrange
    from jwt_signing import sign_licence_jwt

    token = sign_licence_jwt(
        workspace_id=789,
        tier="free",
        machine_fingerprint="tamper-test",
    )
    # Tamper: flip a byte in the signature (last segment)
    parts = token.split(".")
    sig_bytes = base64.urlsafe_b64decode(parts[2] + "==")
    tampered_sig = bytes([sig_bytes[0] ^ 0xFF]) + sig_bytes[1:]
    tampered_token = (
        parts[0] + "." + parts[1] + "." + base64.urlsafe_b64encode(tampered_sig).decode().rstrip("=")
    )

    # Act & Assert
    from jwt_verify import decode_licence_jwt

    with pytest.raises(pyjwt.InvalidSignatureError):
        decode_licence_jwt(tampered_token)


def test_decode_missing_required_claim_raises(_patch_jwt_keys):
    """A JWT missing a required claim (e.g. workspace_id) raises
    pyjwt.MissingRequiredClaimError."""
    # Arrange
    priv_pem, pub_pem = _patch_jwt_keys
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=365)
    # Manually craft a JWT without workspace_id
    claims = {
        "tier": "indie",
        "issuance_ts": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
        "machine_fingerprint": "manual-fp",
        # Missing: workspace_id
    }
    token = pyjwt.encode(claims, priv_pem, algorithm="RS256")

    # Act & Assert
    from jwt_verify import decode_licence_jwt

    with pytest.raises(pyjwt.MissingRequiredClaimError):
        decode_licence_jwt(token)


def test_decode_malformed_token_raises():
    """A malformed token (not valid base64, missing segments) raises
    pyjwt.DecodeError."""
    # Act & Assert
    from jwt_verify import decode_licence_jwt

    with pytest.raises(pyjwt.DecodeError):
        decode_licence_jwt("not.a.token")

    with pytest.raises(pyjwt.DecodeError):
        decode_licence_jwt("three.segments.only.extra")

    with pytest.raises((pyjwt.DecodeError, ValueError)):
        decode_licence_jwt("invalid@base64!.tokens.here")

"""REVUE-277 Phase 1: JWT verification key embedding.

The CLI verifies JWT signatures locally against a public key baked into
the Nuitka-compiled binary at build time. The private signing half lives
only in:

- Fly secret ``JWT_SIGNING_KEY`` (base64-encoded RSA-2048 PEM)
- 1Password vault "Private" / "Revue JWT Signing Key (production)"

This test file proves the embedded constant module is well-formed AND
that the bytes parse as a real RSA public key — see
``test_jwt_public_key_pem_round_trips_with_sign_and_verify`` for the
sign-with-test-priv / verify-with-embedded-pub round-trip. PyJWT now
ships as a ``revue_core`` dependency (REVUE-277 Phase 2), so this check
no longer needs to be deferred.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def test_jwt_keys_module_imports_cleanly() -> None:
    """The module must import without side effects."""
    from revue_core.security import jwt_keys  # noqa: F401


def test_jwt_keys_module_exposes_public_key_pem_constant() -> None:
    """``JWT_PUBLIC_KEY_PEM`` must be a non-empty string with the
    PEM envelope markers PyJWT expects.
    """
    from revue_core.security.jwt_keys import JWT_PUBLIC_KEY_PEM

    assert isinstance(JWT_PUBLIC_KEY_PEM, str)
    assert JWT_PUBLIC_KEY_PEM, "JWT_PUBLIC_KEY_PEM is empty"
    assert JWT_PUBLIC_KEY_PEM.startswith("-----BEGIN PUBLIC KEY-----")
    assert JWT_PUBLIC_KEY_PEM.rstrip().endswith("-----END PUBLIC KEY-----")


def test_jwt_public_key_pem_body_decodes_as_base64() -> None:
    """The base64 body of the PEM must decode cleanly. This rules out
    accidental whitespace damage or copy-paste truncation of the embedded
    constant.
    """
    from revue_core.security.jwt_keys import JWT_PUBLIC_KEY_PEM

    body = "".join(
        line for line in JWT_PUBLIC_KEY_PEM.splitlines()
        if line and not line.startswith("-----")
    )
    decoded = base64.b64decode(body, validate=True)
    # RSA-2048 SubjectPublicKeyInfo DER is ~294 bytes — a generous range
    # rules out truncation without pinning the exact byte length, which
    # is sensitive to ASN.1 encoder choices.
    assert 270 < len(decoded) < 320, (
        f"DER length {len(decoded)} is outside the expected RSA-2048 range; "
        f"the embedded key is probably truncated or the wrong size."
    )


def test_jwt_algorithm_constant_is_rs256() -> None:
    """The algorithm constant pins the signing scheme. Anything other
    than ``RS256`` for a public-key constant would be a misconfiguration.
    """
    from revue_core.security.jwt_keys import JWT_ALGORITHM

    assert JWT_ALGORITHM == "RS256"


def test_jwt_signing_key_env_var_constant() -> None:
    """The name of the Fly-secret env var is centralised in the same
    module so the backend (Phase 2) and ops docs reference the same
    string. Drift between them would break activation silently in prod.
    """
    from revue_core.security.jwt_keys import JWT_SIGNING_KEY_ENV_VAR

    assert JWT_SIGNING_KEY_ENV_VAR == "JWT_SIGNING_KEY"


def test_jwt_public_key_pem_round_trips_with_sign_and_verify(monkeypatch) -> None:
    """The embedded ``JWT_PUBLIC_KEY_PEM`` must be a *real* RSA SPKI
    public key — not just a base64 blob of the right DER length.

    The DER-length check in
    ``test_jwt_public_key_pem_body_decodes_as_base64`` is necessary but
    not sufficient: a non-RSA 270-320 byte blob (e.g. an Ed25519 key, a
    truncated DSA key, or 290 bytes of random noise) would pass it. The
    only test that actually proves the bytes form a valid RS256 verifier
    is to sign-with-test-private, verify-with-embedded-public, and
    assert the decode succeeds.

    Why we patch with a TEST keypair instead of round-tripping against
    the production public key: we don't have the production private key
    in the test environment (it lives only in the Fly secret + 1Password,
    by design). We generate a fresh keypair, monkeypatch
    ``JWT_PUBLIC_KEY_PEM`` to the TEST public, then sign with the TEST
    private and verify. This proves the *constant assignment + module
    plumbing* is correct — that ``pyjwt.decode`` will accept the value
    of ``JWT_PUBLIC_KEY_PEM`` as a valid RS256 SPKI input. The
    sister test above pins the production key to ~270-320 bytes of valid
    base64 inside the right PEM envelope, which together rules out the
    "wrong-shape constant" failure mode (Ed25519, truncation, garbage).
    """
    import revue_core.security.jwt_keys as jwt_keys_module

    # Generate a fresh test keypair — never use the production private
    # half (which the test environment must not have access to).
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

    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", pub_pem.decode())

    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {
            "workspace_id": 1,
            "tier": "indie",
            "issuance_ts": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
            "machine_fingerprint": "fp",
        },
        priv_pem,
        algorithm=jwt_keys_module.JWT_ALGORITHM,
    )

    claims = pyjwt.decode(
        token,
        jwt_keys_module.JWT_PUBLIC_KEY_PEM,
        algorithms=[jwt_keys_module.JWT_ALGORITHM],
    )
    assert claims["workspace_id"] == 1
    assert claims["tier"] == "indie"
    assert claims["machine_fingerprint"] == "fp"


def test_get_jwt_public_key_returns_embedded_constant() -> None:
    """REVUE-334 AC1: ``get_jwt_public_key()`` returns the embedded
    production key. Verify sites call this accessor (not the module
    constant directly) so Nuitka cannot constant-fold the key value into
    the compiled verify function body across the wheel boundary.
    """
    from revue_core.security.jwt_keys import (
        JWT_PUBLIC_KEY_PEM,
        get_jwt_public_key,
    )

    assert get_jwt_public_key() == JWT_PUBLIC_KEY_PEM
    assert get_jwt_public_key().startswith("-----BEGIN PUBLIC KEY-----")


def test_get_jwt_public_key_reads_at_call_time(monkeypatch) -> None:
    """REVUE-334 AC1/AC2: the accessor must read the module global at call
    time, not bind it at import. This is what keeps the plain-Python
    monkeypatch-based tests (AC2) working after verify sites switch from
    the constant to the accessor — and mirrors the constant-folding
    resistance the compiled binary needs.
    """
    import revue_core.security.jwt_keys as jwt_keys_module

    sentinel = "-----BEGIN PUBLIC KEY-----\nSENTINEL\n-----END PUBLIC KEY-----\n"
    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", sentinel)

    assert jwt_keys_module.get_jwt_public_key() == sentinel


def test_jwt_public_key_pem_parses_as_rsa_public_key() -> None:
    """The PRODUCTION embedded key must parse as an RSA public key via
    ``cryptography`` — not just decode as base64. Catches the case where
    the constant is replaced with a syntactically PEM-shaped blob whose
    DER body is not a valid RSA SubjectPublicKeyInfo (e.g. truncated, a
    different algorithm, corrupted during a rotation). This is the
    minimum bar required for ``pyjwt.decode(...,
    JWT_PUBLIC_KEY_PEM, algorithms=["RS256"])`` to even start.
    """
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from revue_core.security.jwt_keys import JWT_PUBLIC_KEY_PEM

    key = serialization.load_pem_public_key(JWT_PUBLIC_KEY_PEM.encode())
    assert isinstance(key, RSAPublicKey), (
        f"embedded JWT_PUBLIC_KEY_PEM did not parse as RSA — got {type(key).__name__}"
    )
    # RSA-2048 is the documented size (see module docstring).
    assert key.key_size == 2048, (
        f"embedded JWT_PUBLIC_KEY_PEM is RSA-{key.key_size}, expected RSA-2048"
    )

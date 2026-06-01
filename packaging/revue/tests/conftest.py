"""Shared pytest fixtures for the revue packaging tests."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent

# Make `revue_skill` importable without a `pip install -e .` round-trip.
SRC = PACKAGING_DIR / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def _rsa_test_keypair() -> tuple[str, str]:
    """One throwaway RSA-2048 keypair for the whole test session.

    Generating an RSA-2048 key is ~100ms; the REVUE-371 JWT tests previously
    regenerated one per test (and per parametrized case), which review #317-a
    flagged as wasteful. Session scope generates it once. Tests still patch the
    embedded public key per-function via ``sign_jwt``, so monkeypatch teardown
    isolation is preserved regardless of the shared key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


@pytest.fixture
def sign_jwt(monkeypatch, _rsa_test_keypair):
    """Patch the embedded JWT public key to the session test key and return a
    signer for valid RS256 licence tokens.

    Call ``sign_jwt(tier="indie", workspace_id=42, exp_offset=86400,
    **extra_claims)`` to get a token the production verifier accepts. Shared
    across test_validate.py and test_support.py (REVUE-371 review #302) so the
    throwaway-key + sign boilerplate lives in one place. Tests that need an
    *invalid* signature (mismatched key) or bespoke malformed claims keep
    generating their own keypair — this is only for the valid-signature path.
    """
    import jwt as pyjwt

    import revue_core.security.jwt_keys as jwt_keys_module

    priv_pem, pub_pem = _rsa_test_keypair
    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", pub_pem)

    def _sign(
        *,
        tier: str = "indie",
        workspace_id: int = 42,
        exp_offset: int = 86400,
        **extra_claims,
    ) -> str:
        claims = {
            "exp": int(time.time()) + exp_offset,
            "workspace_id": workspace_id,
            "tier": tier,
            **extra_claims,
        }
        return pyjwt.encode(claims, priv_pem, algorithm="RS256")

    return _sign

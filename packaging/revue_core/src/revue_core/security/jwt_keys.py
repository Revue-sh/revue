"""JWT verification key — embedded at build time, baked into the Nuitka
binary for offline signature verification.

The CLI (``revue activate <key>`` and the daily-check path) verifies
RS256 JWT signatures against ``JWT_PUBLIC_KEY_PEM`` without any network
round-trip. The signing counterpart is stored only in:

- Fly secret ``JWT_SIGNING_KEY`` (base64-encoded RSA-2048 PEM)
- 1Password vault "Private" / item "Revue JWT Signing Key (production)"

The constants here are **safe to commit and ship**. Rotation procedure
is documented at ``docs/runbooks/jwt-signing-key.md``.
"""
from __future__ import annotations


JWT_ALGORITHM: str = "RS256"
"""Signing scheme. RS256 = RSA-2048 + SHA-256. Asymmetric so the CLI can
verify without holding the private key."""


JWT_SIGNING_KEY_ENV_VAR: str = "JWT_SIGNING_KEY"
"""Name of the Fly-secret environment variable that holds the
base64-encoded private RSA-2048 PEM. Centralised here so the backend
(Phase 2) and ops runbook reference the same string — drift between
them would break activation silently in production."""


JWT_PUBLIC_KEY_PEM: str = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAhYmRYL8VMeSAw9cQI/hG
8ccr6OTUtxRPh3X0oMKyaBUheI/rcq06wCdshHwd7iz05ojv7MP5eLCte9Vjsrs7
RrQOvGcYVcm5eJRSz+SPyvw/+6B65sB+EW0PYVbAjrI0JsbQCqhnsGBHcFm+fC5K
+0hsirvRcfqP7kY3G0OkjMpVBSW82eerCIxeNjBLv5BcutDkXcfgPW4pUC1GDBdF
TdMMWPW0Fn3Bq4lnXpPTCQHAeTLhnnv/5dAysJF45p3XCsZPPptw8AEmrVxNqzQ8
vg/XZqQHWli7jl9zx7DrtCfoLdjJEx+NE+1Jze6Ucu4oHIGqPrvwya+EAr+rfkAh
XwIDAQAB
-----END PUBLIC KEY-----
"""
"""Production JWT verification key — RSA-2048, generated 2026-05-21.

This is the public half. The private half lives in Fly secret
``JWT_SIGNING_KEY`` and in 1Password (see module docstring). Embedding
the public key at build time means the CLI can verify offline, which
is the entire point of the daily-check + cache contract delivered by
REVUE-278."""


def get_jwt_public_key() -> str:
    """Return the embedded JWT public key PEM at call time.

    Using a function call instead of a module-level constant prevents Nuitka
    from constant-folding the value into the compiled _verify_jwt body.
    Every verify site reads the key via this accessor, ensuring the call
    crosses a compiled-module boundary — Nuitka cannot inline the value
    into the caller's machine code. This is REVUE-334 AC1.

    Call this from every verify site; never bind the return value at module
    import time.
    """
    return JWT_PUBLIC_KEY_PEM

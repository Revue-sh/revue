"""RS256 JWT verification for licence validation (REVUE-278).

Counterpart to ``jwt_signing.py``: while signing issues and encodes tokens,
verification decodes and validates them. Separate modules for SRP — the
operations are truly distinct concerns even though they touch the same
cryptographic artefacts.

Verification enforces:
- Valid RS256 signature (``verify_signature=True``)
- Unexpired token (``verify_exp=True``, checks the ``exp`` claim)
- Presence of all required claims (``require=["exp", "workspace_id", "tier"]``)

Failures raise subclasses of ``pyjwt.PyJWTError``:
- ``ExpiredSignatureError`` — token is past its ``exp`` timestamp
- ``InvalidSignatureError`` — signature does not match the public key
- ``MissingRequiredClaimError`` — a required claim is missing
- ``DecodeError`` — malformed token (not valid base64, wrong segment count, etc.)

Callers decide how to handle each failure mode (serve from cache, block
invocation, return 401, etc.).
"""
from __future__ import annotations

from typing import Any, Final

import jwt as pyjwt


# Inlined from ``revue_core.security.jwt_keys`` because the web container
# does not ship revue_core — importing it at module top crashes uvicorn
# (REVUE-345). The CLI keeps the revue_core copy for offline verification;
# the two must stay in sync.
JWT_ALGORITHM: Final[str] = "RS256"

JWT_PUBLIC_KEY_PEM: Final[str] = """\
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


def decode_licence_jwt(token: str) -> dict[str, Any]:
    """Decode and verify a licence JWT. Returns the claims dict if valid;
    raises a ``pyjwt.PyJWTError`` subclass if invalid.

    Enforces:
    - Valid RS256 signature against the embedded public key
    - Token is not expired (``exp`` claim checked)
    - Required claims are present: ``exp``, ``workspace_id``, ``tier``

    Raises:
        pyjwt.ExpiredSignatureError — token is past its expiry
        pyjwt.InvalidSignatureError — signature does not match public key
        pyjwt.MissingRequiredClaimError — required claim missing
        pyjwt.DecodeError — malformed token
        pyjwt.InvalidKeyError — embedded public key is corrupted
    """
    return pyjwt.decode(
        token,
        JWT_PUBLIC_KEY_PEM,
        algorithms=[JWT_ALGORITHM],
        options={
            "verify_signature": True,
            "verify_exp": True,
            "require": ["exp", "workspace_id", "tier"],
        },
    )

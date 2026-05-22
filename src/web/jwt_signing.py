"""RS256 JWT signing for licence activation (REVUE-277 Phase 2).

The private key is loaded from the Fly secret named in
``JWT_SIGNING_KEY_ENV_VAR``. The CLI verifies the resulting JWT against
the ``JWT_PUBLIC_KEY_PEM`` constant in ``revue_core.security.jwt_keys``.

Loading is **lazy** — the env var is read inside ``sign_licence_jwt`` so
tests can monkeypatch it without restarting the FastAPI app. Reading at
import time would also crash uvicorn at boot if the Fly secret is unset,
which is the right failure mode for production but wrong for tests.
"""
from __future__ import annotations

import base64
import binascii
import os
from datetime import datetime, timedelta, timezone
from typing import Final

import jwt as pyjwt


# Inlined from ``revue_core.security.jwt_keys`` because the web container
# does not ship revue_core — importing it at module top crashes uvicorn
# (REVUE-345). The CLI still uses the revue_core copy for verification;
# the two must stay in sync.
JWT_ALGORITHM: Final[str] = "RS256"
JWT_SIGNING_KEY_ENV_VAR: Final[str] = "JWT_SIGNING_KEY"


# 365 days is a deliberate trade-off between forcing online refresh
# (REVUE-278 daily-check delivers the day-grain revocation signal) and
# minimising customer-perceptible expiry events. Pinned here so the
# constant is reviewable in one place.
DEFAULT_EXPIRY_DAYS: Final[int] = 365


class JWTSigningKeyMissing(RuntimeError):
    """Raised at sign time if the Fly secret env var is unset.

    The handler turns this into a 500 ``server_misconfigured`` response
    with a pointer to docs/runbooks/jwt-signing-key.md — operator-error
    visibility, not silent token issuance.
    """


def _load_private_key_pem() -> bytes:
    raw = os.environ.get(JWT_SIGNING_KEY_ENV_VAR)
    if not raw:
        raise JWTSigningKeyMissing(
            f"{JWT_SIGNING_KEY_ENV_VAR} is unset; cannot sign licence JWTs. "
            f"Set the Fly secret via `flyctl secrets set` — see "
            f"docs/runbooks/jwt-signing-key.md."
        )
    # S5: ``validate=True`` rejects non-alphabet characters and missing
    # padding instead of silently stripping them. Without it, operator
    # error (raw PEM pasted with whitespace, truncated base64) would
    # surface as a cryptic PyJWT/cryptography error several frames later.
    try:
        return base64.b64decode(raw, validate=True)
    except binascii.Error as exc:
        raise JWTSigningKeyMissing(
            f"{JWT_SIGNING_KEY_ENV_VAR} is set but is not valid base64 "
            f"({exc}); check the Fly secret encoding. The PEM must be "
            f"base64-encoded with no surrounding whitespace — see "
            f"docs/runbooks/jwt-signing-key.md."
        ) from exc


def sign_licence_jwt(
    *,
    workspace_id: int,
    tier: str,
    machine_fingerprint: str,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    now: datetime | None = None,
) -> str:
    """Sign and return a JWT for the given licence claims.

    Claim set (REVUE-277 AC1):
        - ``workspace_id``: int — which workspace this licence belongs to
        - ``tier``: str — free | indie | pro | enterprise
        - ``issuance_ts``: int — unix ts when signed
        - ``exp``: int — unix ts past which the CLI must re-validate
          (standard PyJWT claim name; ``verify_exp`` enforces it)
        - ``machine_fingerprint``: str — opaque CLI-supplied identifier
          (enforcement deferred to a future story; encoded for future use)

    Raises ``JWTSigningKeyMissing`` if the Fly secret is unset. Caller
    should map this to an operator-visible 5xx, not a generic 500.
    """
    issuance = now or datetime.now(timezone.utc)
    expiry = issuance + timedelta(days=expiry_days)
    claims = {
        "workspace_id": workspace_id,
        "tier": tier,
        "issuance_ts": int(issuance.timestamp()),
        # ``exp`` is the standard PyJWT claim name; the verifier's
        # ``verify_exp`` option only fires when the claim is literally
        # called ``exp``. A non-standard ``expiry_ts`` would have left
        # expiry unenforced — see S1 in the cycle-1 review.
        "exp": int(expiry.timestamp()),
        "machine_fingerprint": machine_fingerprint,
    }
    return pyjwt.encode(claims, _load_private_key_pem(), algorithm=JWT_ALGORITHM)

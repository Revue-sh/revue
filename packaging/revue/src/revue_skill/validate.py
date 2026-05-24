"""``revue-local`` licence validation cache + network contract (REVUE-278).

The CLI POSTs the JWT from ~/.config/revue/licence.jwt to the hardcoded
validate endpoint, caches the server response, and uses the response to gate
the review pipeline. Identical behaviour across all tiers (no graded grace
per AC5).

The cache file is ~/.config/revue/licence-cache.json (mode 0600, parent 0700).
If cache is fresh (< 24h old), the cached result is used and no network call
is made. If cache is stale or missing and the network is unreachable, the
invocation is blocked with a documented message (AC4, exit code 8).

Threat model:

- ``VALIDATE_URL`` is a literal constant. **Never** read from an env var —
  a configurable URL is a licence-bypass vector (same as ``ACTIVATE_URL``).
  See ``docs/runbooks/jwt-signing-key.md`` and project memory
  ``project_license_validator_hardcoded``.
- The cache file stores the raw JSON response (JWT is the trust artefact, no
  encryption needed). File mode 0600, parent directory 0700.
- Network/server errors return documented exit codes — silent failures are
  explicitly forbidden.

Exit code table (extends ``activate.py``'s table):

    0  success (licence valid; review proceeds)
    2  network failure (transient — safe to retry)
    4  unexpected response shape from server
    5  JWT verification failed (signature, claims, or expiry)
    6  server-side misconfiguration (5xx — safe to retry after operator fix)
    7  local file-system failure writing the cache
    8  network failure outside cache window — invocation blocked per AC4

Clock-skew trade-off (decision #4 in PM-plan):
    The server returns ``refresh_after_ts`` as an absolute epoch-seconds
    timestamp (issuance_ts + 86400). The skill uses wall-clock time
    (time.time()) to compare against this horizon. If the client clock is
    skewed, the cache lifetime is bounded by the client's wall-clock drift,
    not the server's. This is acceptable because:
    1. The skill re-validates daily (REVUE-278 daily-check contract)
    2. Server-side anomaly detection (REVUE-350) catches abuse patterns
    3. No client-side cryptographic mitigation works (pyjwt.verify_exp
       is itself wall-clock dependent; time.monotonic doesn't persist
       across process restarts)

The ``refresh_after_ts`` is server-issued and canonical — the skill trusts it
even if the client clock is skewed.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

import httpx
import jwt as pyjwt

# Import the module, not the constant, so the value is read at call time.
# Same pattern as ``activate.py``.
from revue_core.security import jwt_keys as _jwt_keys

VALIDATE_URL: Final[str] = "https://revue.sh/api/v2/licence/validate"
"""Production validation endpoint. Hardcoded — never read from an env var.
See module docstring for the threat-model rationale."""

# Cache window: 24 hours in seconds. Identical for all tiers (AC5).
CACHE_WINDOW_SECONDS: Final[int] = 86400

_CACHE_DIR_PERMS: Final[int] = 0o700
_CACHE_FILE_PERMS: Final[int] = 0o600
_CACHE_FILENAME: Final[str] = "licence-cache.json"


def _get_cache_path() -> Path:
    """Return the path to the licence cache file. Test-overridable via
    REVUE_LICENCE_CACHE_PATH env var; production uses ~/.config/revue/."""
    env_override = os.environ.get("REVUE_LICENCE_CACHE_PATH")
    if env_override:
        return Path(env_override)
    return Path.home() / ".config" / "revue" / _CACHE_FILENAME


def _build_http_client() -> httpx.Client:
    """Construct the httpx.Client used to POST to ``VALIDATE_URL``.

    Factored so tests can monkeypatch it with a mock transport. Real callers
    get a fresh client with a sane timeout."""
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))


def _read_cache() -> dict | None:
    """Read the cache file if it exists and is parseable. Returns None if
    missing, unreadable, or malformed JSON."""
    cache_path = _get_cache_path()
    try:
        with open(cache_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_cache_fresh(cache_data: object) -> bool:
    """Check if the cached result is still fresh (within cache window).

    Fresh iff cache_data is a dict that:
    - self-reports ``valid: true``,
    - has numeric ``refresh_after_ts`` (server-issued horizon),
    - has numeric ``cached_at`` (client-stamped at write time), AND
    - now < min(refresh_after_ts, cached_at + CACHE_WINDOW_SECONDS).

    The upper-bound cap anchored to ``cached_at`` is defense-in-depth
    against cache tampering: even if a local attacker writes a far-future
    ``refresh_after_ts`` to the plaintext cache file, the skill will still
    re-validate at most 24h after the recorded write time. A missing or
    forged ``cached_at`` short-circuits to "not fresh".
    """
    if not isinstance(cache_data, dict):
        return False
    if not cache_data.get("valid"):
        return False
    refresh_after_ts = cache_data.get("refresh_after_ts")
    cached_at = cache_data.get("cached_at")
    if not isinstance(refresh_after_ts, (int, float)):
        return False
    if not isinstance(cached_at, (int, float)):
        return False
    now = time.time()
    capped_horizon = min(refresh_after_ts, cached_at + CACHE_WINDOW_SECONDS)
    return now < capped_horizon


def _write_cache(response_json: dict) -> int:
    """Write the validation response to the cache file. Returns 0 on success,
    exit code 7 on failure. Creates parent directory if needed, ensures file
    mode 0600 and parent mode 0700.

    Stamps ``cached_at`` (client wall-clock at write time) so the freshness
    check can cap the cache lifetime independently of the server-issued
    ``refresh_after_ts`` (defense against cache tampering)."""
    cache_path = _get_cache_path()
    cache_dir = cache_path.parent

    payload = {**response_json, "cached_at": int(time.time())}

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(cache_dir, _CACHE_DIR_PERMS)

        # Write atomically via temp file (same pattern as activate.py)
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(cache_dir), prefix=".cache-", suffix=".tmp"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.chmod(tmp_path, _CACHE_FILE_PERMS)
            tmp_path.replace(cache_path)
        except BaseException:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return 0
    except (PermissionError, OSError) as exc:
        cache_dir_path = Path.home() / ".config" / "revue"
        print(
            f"error: could not write licence cache under {cache_dir_path} "
            f"({exc.__class__.__name__}: {exc}). Check that the directory is "
            f"writable by your user, or set $HOME to a writable location.",
            file=sys.stderr,
        )
        return 7


_BLOCK_MESSAGE: Final[str] = (
    "Revue needs to verify your licence — check connection or run `revue activate`."
)


def validate_licence(jwt_token: str) -> int:
    """Run the licence validation flow. Returns the process exit code
    (0 on success, non-zero on failure per the exit-code table).

    The flow is:
    1. Check if cache is fresh (within refresh_after_ts) → return 0
    2. Cache stale/missing → POST JWT to validate endpoint
    3. On 200 + valid=true → write cache, return 0
    4. On 200 + valid=false → block with exit 5 (server rejected the JWT)
    5. On network fail (any cache state) → block with exit 8 per AC4 — step 1
       already returned 0 for fresh cache, so reaching here means stale/missing
    """
    # Step 1: check cache
    cache_data = _read_cache()
    if _is_cache_fresh(cache_data):
        # Cache is fresh — no network call, invocation proceeds (AC2)
        return 0

    # Step 2: cache is stale or missing; attempt network call.
    # NOTE: reaching this branch means the cache is NOT fresh, so any
    # network failure here is outside the 24h window → AC4 blocks. The
    # AC3 "graceful in-window" path is satisfied by step 1 returning 0
    # before the network is ever touched.
    payload = {"jwt": jwt_token}
    try:
        with _build_http_client() as client:
            resp = client.post(VALIDATE_URL, json=payload)
    except httpx.ConnectError as exc:
        print(
            f"error: {_BLOCK_MESSAGE} (could not reach {VALIDATE_URL}: {exc})",
            file=sys.stderr,
        )
        return 8
    except httpx.HTTPError as exc:
        print(
            f"error: {_BLOCK_MESSAGE} (network failure talking to "
            f"{VALIDATE_URL}: {exc})",
            file=sys.stderr,
        )
        return 8

    # Step 3: got a response; check status
    if resp.status_code != 200:
        try:
            body = resp.json()
        except Exception:
            body = {}
        error_code = body.get("error", f"http_{resp.status_code}")
        message = body.get("message", f"server returned status {resp.status_code}")
        print(
            f"error: validation failed ({error_code}): {message}",
            file=sys.stderr,
        )
        return 6 if 500 <= resp.status_code < 600 else 4

    # Step 4: 200 response; parse body
    try:
        body = resp.json()
    except Exception as exc:
        print(
            f"error: server returned an unexpected response shape: {exc}",
            file=sys.stderr,
        )
        return 4

    if not isinstance(body, dict):
        print(
            f"error: server returned an unexpected response shape "
            f"(expected object, got {type(body).__name__})",
            file=sys.stderr,
        )
        return 4

    # Step 5: enforce server's valid verdict — revocation/expired/tampered
    # JWTs return {"valid": false} with HTTP 200 (see api_routes.py
    # /v2/licence/validate). The cached "valid: false" must NEVER be persisted
    # — otherwise a stale-cache + offline path would reanimate it on the next
    # invocation.
    if not body.get("valid"):
        error_code = body.get("error", "invalid_licence")
        message = body.get(
            "message",
            "Server reports your licence is no longer valid. "
            "Run `revue activate` to re-issue.",
        )
        print(
            f"error: licence validation failed ({error_code}): {message}",
            file=sys.stderr,
        )
        return 5

    # Step 6: valid response; write cache and return 0
    cache_result = _write_cache(body)
    if cache_result != 0:
        return cache_result

    # Handle refreshed_jwt if present (decision #5). Rotation policy is
    # currently a hook — server returns None for now; this block exists so
    # turning rotation on server-side requires no client change.
    #
    # SECURITY: verify the refreshed JWT's signature against the embedded
    # public key BEFORE writing it to disk. Without this check a compromised
    # backend (or MITM via a corp-installed root CA) could overwrite the
    # user's working licence with garbage, locking them out until a manual
    # ``revue activate``. Same threat model as ``activate.py:_verify_jwt``.
    refreshed_jwt = body.get("refreshed_jwt")
    if refreshed_jwt:
        try:
            pyjwt.decode(
                refreshed_jwt,
                _jwt_keys.JWT_PUBLIC_KEY_PEM,
                algorithms=[_jwt_keys.JWT_ALGORITHM],
                options={
                    "verify_exp": True,
                    "require": ["exp", "workspace_id", "tier"],
                },
            )
        except pyjwt.PyJWTError as exc:
            print(
                f"warning: server returned an invalid refreshed JWT "
                f"({exc.__class__.__name__}: {exc}); ignoring. Existing "
                f"licence remains in place.",
                file=sys.stderr,
            )
            return 0

        try:
            licence_path = Path.home() / ".config" / "revue" / "licence.jwt"
            licence_path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(licence_path.parent, _CACHE_DIR_PERMS)

            fd, tmp_path_str = tempfile.mkstemp(
                dir=str(licence_path.parent), prefix=".jwt-", suffix=".tmp"
            )
            tmp_path = Path(tmp_path_str)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(refreshed_jwt)
                os.chmod(tmp_path, _CACHE_FILE_PERMS)
                tmp_path.replace(licence_path)
            except BaseException:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        except (PermissionError, OSError) as exc:
            # Surface a warning so operators can diagnose; don't block — the
            # old JWT is still valid until its own exp claim. Cache write
            # already succeeded so next invocation will hit the cache.
            print(
                f"warning: could not persist refreshed licence JWT "
                f"({exc.__class__.__name__}: {exc}). Existing JWT remains "
                f"valid until its expiry.",
                file=sys.stderr,
            )

    return 0

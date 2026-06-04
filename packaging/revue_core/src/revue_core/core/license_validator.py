"""
License key validation for Revue.

Validates the REVUE_LICENSE_KEY against the Revue API on orchestrator startup.
Supports a 72-hour offline grace period via a local cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from revue_core.core.logging_channels import Log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALIDATE_URL = "https://revue.sh/api/license/validate"
CACHE_PATH = Path("/tmp/.revue_license_cache.json")
CACHE_TTL_SECONDS = 72 * 3600  # 72 hours

# Transient-failure retry budget. Hardcoded — NOT env-overridable, consistent with
# VALIDATE_URL being baked into the compiled binary. A CI-controllable attempt count
# would be a license-bypass / denial-of-validation surface, and an unbounded budget
# would let CI hang.
MAX_VALIDATION_ATTEMPTS = 3  # total attempts before falling through to offline cache
RETRY_BACKOFF_BASE_SECONDS = 0.5  # first inter-attempt sleep
RETRY_BACKOFF_CAP_SECONDS = 4.0  # upper bound on exponential backoff

TIER_ALL_AGENTS = [
    "orchestrator",
    "code-quality-expert",
    "security-expert",
    "performance-expert",
    "architecture-expert",
    "consolidator",
    "sage",
    "cleo",
    "nova",
    "vex",
]

AGENTS_BY_TIER: dict[str, list[str]] = {
    "free": ["orchestrator", "code-quality-expert", "consolidator"],
    "indie": TIER_ALL_AGENTS,
    "pro": TIER_ALL_AGENTS,
    "enterprise_starter": TIER_ALL_AGENTS,
    "enterprise_growth": TIER_ALL_AGENTS,
    "enterprise_plus": TIER_ALL_AGENTS,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LicenseError(RuntimeError):
    """Raised when license validation fails — hard stop."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class _ValidationResult:
    """Result of a single license API validation call (one attempt).

    Encapsulates the outcome of calling the API endpoint once, without retry logic.
    Supports both successful responses (status_code=200) and error states (network
    errors, non-2xx, etc.).

    Attributes:
        status_code: HTTP status code from the response, or None if network error.
        body: Parsed JSON response body, or None if network error or unparseable.
        error: Error message (network, parsing, etc.), or None if successful.
    """

    status_code: int | None
    body: dict | None
    error: str | None

    @property
    def is_success(self) -> bool:
        """True if the API call succeeded (status 200, valid response parsed)."""
        return self.status_code == 200 and self.body is not None


@dataclass
class LicenseInfo:
    """Validated license information returned by the API."""

    valid: bool
    tier: str
    agents_allowed: list[str]
    reviews_left: Optional[int]  # None == unlimited
    expires_at: str
    key: str = field(repr=False)  # never log the raw key

    @property
    def reviews_left_display(self) -> str:
        """Human-readable review count for logging."""
        if self.reviews_left is None:
            return "unlimited reviews"
        n = self.reviews_left
        return f"{n} review{'s' if n != 1 else ''} remaining"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(
    license_key: str | None = None,
    repo_id: str = "",
    ci_run_id: str = "",
    *,
    _http_client: httpx.Client | None = None,
) -> LicenseInfo:
    """Validate the license key against the Revue API.

    Falls back to a local cache for up to 72h when the API is unreachable.
    Raises :class:`LicenseError` on hard failures (invalid key, expired cache).
    
    Supports REVUE_TIER_OVERRIDE for testing in non-production environments:
    - Set REVUE_TIER_OVERRIDE=pro (or free/indie/enterprise_starter/etc.)
    - Only honoured when APP_ENV != "production"
    - Skips API call and returns mock LicenseInfo with tier's agents_allowed

    Args:
        license_key: The REVUE_LICENSE_KEY value. Falls back to the
            ``REVUE_LICENSE_KEY`` env var when *None*.
        repo_id: Repository identifier sent with the validation request.
        ci_run_id: CI run identifier sent with the validation request.
        _http_client: Injected httpx.Client for testing — do NOT use in prod.
    """
    # REVUE_TIER_OVERRIDE: bypass license API for testing (dev/staging only)
    # Security: Only allowed when running from source code (not compiled builds)
    # and APP_ENV is explicitly "development" or "staging"
    is_compiled = getattr(sys, "frozen", False) or "__compiled__" in globals()
    app_env = os.environ.get("APP_ENV", "").lower()
    tier_override = os.environ.get("REVUE_TIER_OVERRIDE", "").lower()
    
    if not is_compiled and app_env in {"development", "staging"} and tier_override in AGENTS_BY_TIER:
        Log.cli.warning(
            f"REVUE_TIER_OVERRIDE={tier_override} active (APP_ENV={app_env}). "
            "For testing only — disabled in production builds."
        )
        return LicenseInfo(
            valid=True,
            tier=tier_override,
            agents_allowed=AGENTS_BY_TIER[tier_override],
            reviews_left=None,  # unlimited for override
            expires_at="9999-12-31T00:00:00Z",
            key="tier-override",
        )
    
    key = license_key or os.environ.get("REVUE_LICENSE_KEY", "")
    if not key:
        raise LicenseError(
            "No Revue license key found. Set the REVUE_LICENSE_KEY environment "
            "variable or add it to your .revue.yml. "
            "Get a key at https://revue.sh/signup"
        )

    try:
        response_data = _call_api_with_retry(key, repo_id, ci_run_id, _http_client)
        _write_cache(key, response_data)
        return _build_license_info(key, response_data)
    except _APIUnreachable:
        Log.cli.warning("Revue API unreachable — checking offline grace period cache.")
        cached = _read_cache(key)
        if cached is None:
            raise LicenseError(
                "Revue API is unreachable and no local license cache was found. "
                "Please check your network connection and try again. "
                "If this persists, contact support at https://revue.sh/support"
            )
        age_hours = (time.time() - cached["cached_at"]) / 3600
        if age_hours > 72:
            raise LicenseError(
                f"Revue API is unreachable and the offline grace period has expired "
                f"({age_hours:.1f}h since last successful validation; limit is 72h). "
                "Please restore network access to the Revue API and try again."
            )
        Log.cli.info("Using cached license (%.1fh old, within 72h grace period).", age_hours)
        return _build_license_info(key, cached["response"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _APIUnreachable(Exception):
    """Raised internally when the API cannot be reached (network error, timeout, bad status).

    ``retryable`` distinguishes transient failures (network errors, 5xx) — which the
    retry loop will re-attempt — from definitive non-2xx client errors (e.g. 404),
    which are unreachable-equivalent but must NOT consume the retry budget.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _call_api(
    key: str,
    repo_id: str,
    ci_run_id: str,
    client: httpx.Client | None,
) -> _ValidationResult:
    """POST to the license validation endpoint once. Returns a result object.

    Performs the HTTP call and builds a :class:`_ValidationResult` without making
    any retry or error-decision logic. The result encapsulates the raw call outcome
    (network error, status code, parsed body). Retryability and raising decisions
    are owned by :func:`_call_api_with_retry`.
    """
    payload = {"key": key, "repo_id": repo_id, "ci_run_id": ci_run_id}
    Log.cli.debug("Calling license API: %s", VALIDATE_URL)
    try:
        if client is not None:
            resp = client.post(VALIDATE_URL, json=payload, timeout=10.0)
        else:
            with httpx.Client(timeout=10.0) as c:
                resp = c.post(VALIDATE_URL, json=payload)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        # Network error — return result with error set, no exception raised here
        return _ValidationResult(status_code=None, body=None, error=str(exc))

    # Attempt to parse JSON; if it fails, return error result
    try:
        data: dict = resp.json()
    except Exception as exc:
        return _ValidationResult(
            status_code=resp.status_code, body=None, error=f"JSON parse error: {exc}"
        )

    return _ValidationResult(status_code=resp.status_code, body=data, error=None)


def _compute_backoff(attempt: int) -> float:
    """Compute exponential backoff duration for a given attempt number.

    Implements exponential backoff: base_seconds * 2^(attempt - 1), capped at
    :data:`RETRY_BACKOFF_CAP_SECONDS`. Pure function — no side effects, no retries.

    Args:
        attempt: The attempt number (1-indexed).

    Returns:
        The backoff duration in seconds, bounded by the hardcoded cap.
    """
    uncapped = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    return min(uncapped, RETRY_BACKOFF_CAP_SECONDS)


def _call_api_with_retry(
    key: str,
    repo_id: str,
    ci_run_id: str,
    client: httpx.Client | None,
) -> dict:
    """Call :func:`_call_api`, retrying transient failures with bounded backoff.

    Inspects the :class:`_ValidationResult` from :func:`_call_api` and decides:
      * On success (200, valid response body): return the body dict.
      * On definitive failures (401/403, ``valid: false``): raise :class:`LicenseError`.
      * On transient failures (network errors, 5xx): retry up to :data:`MAX_VALIDATION_ATTEMPTS`
        total attempts with exponential backoff capped at :data:`RETRY_BACKOFF_CAP_SECONDS`.
      * On non-retryable 4xx (except 401/403): treat as unreachable (no retry) and re-raise
        as :class:`_APIUnreachable` for caller to handle offline cache.

    Returns the validated response body dict on success.
    Raises :class:`LicenseError` on definitive failures, or :class:`_APIUnreachable`
    on unreachable outcomes (to trigger offline grace-period fallback).
    """
    last_unreachable: _APIUnreachable | None = None

    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        result = _call_api(key, repo_id, ci_run_id, client)

        # Success case: 200 status with valid body
        if result.is_success:
            data = result.body
            assert data is not None  # is_success guarantees non-None body
            # Check if the API says the key is invalid
            if not data.get("valid"):
                msg = data.get("message", "License key rejected by Revue API.")
                raise LicenseError(
                    f"License validation failed: {msg} "
                    "Visit https://revue.sh/account to manage your license."
                )
            return data

        # Definitive rejection: 401 or 403 (invalid/revoked key)
        if result.status_code in (401, 403):
            resp_text = result.body or {}
            raise LicenseError(
                f"License key is invalid or has been revoked (HTTP {result.status_code}). "
                f"Response: {str(resp_text)[:200]}. "
                "Please check your REVUE_LICENSE_KEY or visit https://revue.sh/account"
            )

        # Transient failures: network errors (status_code=None) and 5xx
        is_transient = (result.status_code is None) or (result.status_code >= 500)

        if is_transient and attempt < MAX_VALIDATION_ATTEMPTS:
            # Retry: compute backoff and sleep
            backoff = _compute_backoff(attempt)
            error_msg = (
                result.error
                if result.error
                else f"Server error (HTTP {result.status_code})"
            )
            Log.cli.debug(
                "License API transient failure (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                MAX_VALIDATION_ATTEMPTS,
                error_msg,
                backoff,
            )
            time.sleep(backoff)
            continue

        # Non-transient or exhausted retries: treat as unreachable
        error_msg = (
            result.error
            if result.error
            else f"Unexpected status {result.status_code}"
        )
        last_unreachable = _APIUnreachable(error_msg, retryable=False)
        raise last_unreachable

    # Unreachable in practice — the loop either returns or raises — but keeps mypy happy.
    raise last_unreachable  # type: ignore[misc]


def _build_license_info(key: str, data: dict) -> LicenseInfo:
    """Construct a :class:`LicenseInfo` from a raw API response dict."""
    tier = data.get("tier", "free")
    agents_allowed: list[str] = data.get(
        "agents_allowed", AGENTS_BY_TIER.get(tier, AGENTS_BY_TIER["free"])
    )
    reviews_left: Optional[int] = data.get("reviews_left")  # None == unlimited
    expires_at: str = data.get("expires_at", "")

    return LicenseInfo(
        valid=True,
        tier=tier,
        agents_allowed=agents_allowed,
        reviews_left=reviews_left,
        expires_at=expires_at,
        key=key,
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _write_cache(key: str, response: dict) -> None:
    cache = {
        "key_hash": _key_hash(key),
        "response": response,
        "cached_at": time.time(),
    }
    try:
        CACHE_PATH.write_text(json.dumps(cache))
    except OSError as exc:
        Log.cli.warning("Could not write license cache: %s", exc)


def _read_cache(key: str) -> dict | None:
    """Return the cache dict if it matches *key*, else None."""
    try:
        raw = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("key_hash") != _key_hash(key):
        return None
    return raw

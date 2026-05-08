"""
License key validation for Revue.io.

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

from revue.core.logging_channels import Log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALIDATE_URL = "https://revue-io.fly.dev/api/license/validate"
CACHE_PATH = Path("/tmp/.revue_license_cache.json")
CACHE_TTL_SECONDS = 72 * 3600  # 72 hours

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
            "Get a key at https://revue.io/signup"
        )

    try:
        response_data = _call_api(key, repo_id, ci_run_id, _http_client)
        _write_cache(key, response_data)
        return _build_license_info(key, response_data)
    except _APIUnreachable:
        Log.cli.warning("Revue API unreachable — checking offline grace period cache.")
        cached = _read_cache(key)
        if cached is None:
            raise LicenseError(
                "Revue API is unreachable and no local license cache was found. "
                "Please check your network connection and try again. "
                "If this persists, contact support at https://revue.io/support"
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
    """Raised internally when the API cannot be reached (network error, timeout)."""


def _call_api(
    key: str,
    repo_id: str,
    ci_run_id: str,
    client: httpx.Client | None,
) -> dict:
    """POST to the license validation endpoint. Returns the parsed JSON response."""
    payload = {"key": key, "repo_id": repo_id, "ci_run_id": ci_run_id}
    Log.cli.debug("Calling license API: %s", VALIDATE_URL)
    try:
        if client is not None:
            resp = client.post(VALIDATE_URL, json=payload, timeout=10.0)
        else:
            with httpx.Client(timeout=10.0) as c:
                resp = c.post(VALIDATE_URL, json=payload)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        raise _APIUnreachable(str(exc)) from exc

    if resp.status_code == 401 or resp.status_code == 403:
        raise LicenseError(
            f"License key is invalid or has been revoked (HTTP {resp.status_code}). "
            f"Response: {resp.text[:200]}. "
            "Please check your REVUE_LICENSE_KEY or visit https://revue.io/account"
        )
    if resp.status_code != 200:
        raise _APIUnreachable(f"Unexpected status {resp.status_code}")

    data: dict = resp.json()
    if not data.get("valid"):
        msg = data.get("message", "License key rejected by Revue API.")
        raise LicenseError(
            f"License validation failed: {msg} "
            "Visit https://revue.io/account to manage your license."
        )
    return data


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

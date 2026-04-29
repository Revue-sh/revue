"""Tests for revue.core.license_validator — no real HTTP calls."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from revue.core.license_validator import (
    CACHE_PATH,
    CACHE_TTL_SECONDS,
    LicenseError,
    LicenseInfo,
    _key_hash,
    _read_cache,
    _write_cache,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_client(status_code: int = 200, body: dict | None = None) -> httpx.Client:
    """Return a mock httpx.Client that returns a canned response."""
    if body is None:
        body = {
            "valid": True,
            "tier": "pro",
            "agents_allowed": ["orchestrator", "code-quality-expert", "consolidator"],
            "reviews_left": None,
            "expires_at": "2027-01-01T00:00:00Z",
        }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp
    return mock_client


def _clear_cache() -> None:
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()


# ---------------------------------------------------------------------------
# validate() — happy path
# ---------------------------------------------------------------------------

class TestValidateHappyPath:
    def setup_method(self):
        _clear_cache()

    def test_returns_license_info(self):
        client = _make_http_client()
        info = validate("test-key-123", _http_client=client)
        assert isinstance(info, LicenseInfo)
        assert info.valid is True
        assert info.tier == "pro"
        assert info.reviews_left is None

    def test_posts_correct_payload(self):
        client = _make_http_client()
        validate("my-key", repo_id="org/repo", ci_run_id="run-42", _http_client=client)
        call_kwargs = client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["key"] == "my-key"
        assert payload["repo_id"] == "org/repo"
        assert payload["ci_run_id"] == "run-42"

    def test_writes_cache_on_success(self):
        client = _make_http_client()
        validate("key-abc", _http_client=client)
        assert CACHE_PATH.exists()
        cached = json.loads(CACHE_PATH.read_text())
        assert cached["key_hash"] == _key_hash("key-abc")
        assert "response" in cached
        assert "cached_at" in cached

    def test_free_tier_has_limited_agents(self):
        body = {
            "valid": True,
            "tier": "free",
            "agents_allowed": ["orchestrator", "code-quality-expert", "consolidator"],
            "reviews_left": 25,
            "expires_at": "2027-01-01T00:00:00Z",
        }
        client = _make_http_client(body=body)
        info = validate("free-key", _http_client=client)
        assert info.tier == "free"
        assert info.reviews_left == 25
        assert "orchestrator" in info.agents_allowed
        assert len(info.agents_allowed) == 3

    def test_unlimited_reviews_when_null(self):
        client = _make_http_client()
        info = validate("pro-key", _http_client=client)
        assert info.reviews_left is None

    def test_env_var_used_when_key_not_passed(self, monkeypatch):
        monkeypatch.setenv("REVUE_LICENSE_KEY", "env-key-xyz")
        client = _make_http_client()
        info = validate(_http_client=client)
        payload = client.post.call_args[1]["json"]
        assert payload["key"] == "env-key-xyz"


# ---------------------------------------------------------------------------
# validate() — invalid / rejected key
# ---------------------------------------------------------------------------

class TestValidateInvalidKey:
    def setup_method(self):
        _clear_cache()

    def test_raises_license_error_on_401(self):
        client = _make_http_client(status_code=401)
        with pytest.raises(LicenseError, match="invalid or has been revoked"):
            validate("bad-key", _http_client=client)

    def test_raises_license_error_on_403(self):
        client = _make_http_client(status_code=403)
        with pytest.raises(LicenseError, match="invalid or has been revoked"):
            validate("bad-key", _http_client=client)

    def test_raises_license_error_when_valid_false(self):
        body = {"valid": False, "message": "Key has expired"}
        client = _make_http_client(status_code=200, body=body)
        with pytest.raises(LicenseError, match="Key has expired"):
            validate("expired-key", _http_client=client)

    def test_raises_license_error_when_no_key(self, monkeypatch):
        monkeypatch.delenv("REVUE_LICENSE_KEY", raising=False)
        with pytest.raises(LicenseError, match="No Revue license key found"):
            validate(None)


# ---------------------------------------------------------------------------
# validate() — offline grace period
# ---------------------------------------------------------------------------

class TestOfflineGracePeriod:
    def setup_method(self):
        _clear_cache()

    def _unreachable_client(self) -> httpx.Client:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        return mock_client

    def test_uses_fresh_cache_when_api_unreachable(self):
        # First: prime cache with a valid response
        good_client = _make_http_client()
        validate("cached-key", _http_client=good_client)

        # Then: simulate API unreachable — should use cache
        info = validate("cached-key", _http_client=self._unreachable_client())
        assert info.valid is True
        assert info.tier == "pro"

    def test_raises_when_cache_missing_and_api_unreachable(self):
        with pytest.raises(LicenseError, match="no local license cache"):
            validate("no-cache-key", _http_client=self._unreachable_client())

    def test_raises_when_cache_expired(self):
        # Write a stale cache entry (73h ago)
        stale_response = {
            "valid": True,
            "tier": "indie",
            "agents_allowed": [],
            "reviews_left": 5,
            "expires_at": "2027-01-01T00:00:00Z",
        }
        stale_cache = {
            "key_hash": _key_hash("stale-key"),
            "response": stale_response,
            "cached_at": time.time() - (73 * 3600),
        }
        CACHE_PATH.write_text(json.dumps(stale_cache))

        with pytest.raises(LicenseError, match="grace period has expired"):
            validate("stale-key", _http_client=self._unreachable_client())

    def test_cache_ignores_wrong_key(self):
        # Cache belongs to a different key
        good_client = _make_http_client()
        validate("different-key", _http_client=good_client)

        with pytest.raises(LicenseError, match="no local license cache"):
            validate("wrong-key", _http_client=self._unreachable_client())

    def test_timeout_treated_as_unreachable(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")

        with pytest.raises(LicenseError, match="no local license cache"):
            validate("timeout-key", _http_client=mock_client)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

class TestCacheHelpers:
    def setup_method(self):
        _clear_cache()

    def test_write_and_read_cache(self):
        response = {"valid": True, "tier": "indie", "reviews_left": 10}
        _write_cache("mykey", response)
        result = _read_cache("mykey")
        assert result is not None
        assert result["response"]["tier"] == "indie"

    def test_read_cache_returns_none_for_missing_file(self):
        assert _read_cache("nonexistent") is None

    def test_read_cache_returns_none_for_wrong_key(self):
        _write_cache("rightkey", {"valid": True})
        assert _read_cache("wrongkey") is None

    def test_key_hash_is_deterministic(self):
        assert _key_hash("abc") == _key_hash("abc")
        assert _key_hash("abc") != _key_hash("xyz")


# ---------------------------------------------------------------------------
# REVUE-82: REVUE_TIER_OVERRIDE tests
# ---------------------------------------------------------------------------

class TestTierOverride:
    """Tests for REVUE_TIER_OVERRIDE (non-production testing feature)."""

    def test_tier_override_pro_returns_pro_agents(self, monkeypatch):
        """REVUE_TIER_OVERRIDE=pro returns Pro-tier agents without API call."""
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "pro")
        
        # No license key needed when override active
        result = validate(license_key=None, _http_client=None)
        
        assert result.tier == "pro"
        assert result.valid is True
        assert result.reviews_left is None  # unlimited
        assert "orchestrator" in result.agents_allowed
        assert "security-expert" in result.agents_allowed
        assert result.key == "tier-override"

    def test_tier_override_free_returns_free_agents(self, monkeypatch):
        """REVUE_TIER_OVERRIDE=free returns free-tier agents."""
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "free")
        
        result = validate(license_key=None, _http_client=None)
        
        assert result.tier == "free"
        assert len(result.agents_allowed) == 3  # free tier = 3 agents
        assert "orchestrator" in result.agents_allowed
        assert "code-quality-expert" in result.agents_allowed
        assert "security-expert" not in result.agents_allowed

    def test_tier_override_ignored_in_production(self, monkeypatch):
        """REVUE_TIER_OVERRIDE is ignored when APP_ENV=production."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "pro")
        # Clear REVUE_LICENSE_KEY so the env-var fallback in validate() also has no key
        monkeypatch.delenv("REVUE_LICENSE_KEY", raising=False)

        # Should raise LicenseError (no key provided, override ignored)
        with pytest.raises(LicenseError, match="No Revue license key found"):
            validate(license_key=None, _http_client=None)

    def test_tier_override_ignored_without_explicit_dev_staging(self, monkeypatch):
        """REVUE_TIER_OVERRIDE requires APP_ENV=development or staging."""
        monkeypatch.setenv("APP_ENV", "test")  # Not development or staging
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "pro")
        monkeypatch.delenv("REVUE_LICENSE_KEY", raising=False)

        # Should raise LicenseError (override not active)
        with pytest.raises(LicenseError, match="No Revue license key found"):
            validate(license_key=None, _http_client=None)

    def test_tier_override_ignored_in_compiled_build(self, monkeypatch):
        """REVUE_TIER_OVERRIDE is ignored in compiled builds (Nuitka)."""
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "pro")
        monkeypatch.delenv("REVUE_LICENSE_KEY", raising=False)

        # Simulate compiled build
        import sys
        original_frozen = getattr(sys, "frozen", None)
        try:
            sys.frozen = True  # Nuitka sets this

            # Should raise LicenseError (override disabled in compiled builds)
            with pytest.raises(LicenseError, match="No Revue license key found"):
                validate(license_key=None, _http_client=None)
        finally:
            # Restore original state
            if original_frozen is None:
                delattr(sys, "frozen")
            else:
                sys.frozen = original_frozen

    def test_tier_override_invalid_tier_falls_back_to_api(self, monkeypatch):
        """Invalid REVUE_TIER_OVERRIDE value is ignored, API call proceeds."""
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "invalid-tier")
        monkeypatch.delenv("REVUE_LICENSE_KEY", raising=False)

        # Should proceed to API call logic (which raises error due to no key)
        with pytest.raises(LicenseError, match="No Revue license key found"):
            validate(license_key=None, _http_client=None)

    def test_tier_override_case_insensitive(self, monkeypatch):
        """REVUE_TIER_OVERRIDE is case-insensitive."""
        monkeypatch.setenv("APP_ENV", "staging")
        monkeypatch.setenv("REVUE_TIER_OVERRIDE", "PRO")  # uppercase

        result = validate(license_key=None, _http_client=None)

        assert result.tier == "pro"  # normalized to lowercase


# ---------------------------------------------------------------------------
# LicenseInfo.reviews_left_display
# ---------------------------------------------------------------------------

class TestReviewsLeftDisplay:
    def _make_info(self, reviews_left: int | None) -> LicenseInfo:
        return LicenseInfo(
            valid=True,
            tier="pro",
            agents_allowed=["orchestrator"],
            reviews_left=reviews_left,
            expires_at="2099-01-01",
            key="lic_test",
        )

    def test_none_returns_unlimited(self) -> None:
        assert self._make_info(None).reviews_left_display == "unlimited reviews"

    def test_zero_returns_singular(self) -> None:
        assert self._make_info(0).reviews_left_display == "0 reviews remaining"

    def test_one_returns_singular(self) -> None:
        assert self._make_info(1).reviews_left_display == "1 review remaining"

    def test_many_returns_plural(self) -> None:
        assert self._make_info(5).reviews_left_display == "5 reviews remaining"

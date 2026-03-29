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

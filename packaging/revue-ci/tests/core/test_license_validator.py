"""Tests for revue.core.license_validator — no real HTTP calls."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from revue_core.core.license_validator import (
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


def test_validate_url_points_at_production_revue_sh() -> None:
    """`VALIDATE_URL` is hardcoded to the production revue.sh endpoint — pins REVUE-314 AC6."""
    from revue_core.core.license_validator import VALIDATE_URL

    assert VALIDATE_URL == "https://revue.sh/api/license/validate"


def test_license_validator_source_has_no_validate_url_env_var() -> None:
    """Source inspection — `REVUE_VALIDATE_URL` must not appear in this module.

    Pins the absence of the license-bypass surface. The constant must be a bare string
    literal, never sourced from env (even via `f"https://{_HOST}/api/license/validate"`
    style indirection).
    """
    import inspect

    import revue_core.core.license_validator as module
    source = inspect.getsource(module)

    assert "REVUE_VALIDATE_URL" not in source


# ---------------------------------------------------------------------------
# REVUE-397: retry transient API-unreachable before failing
# ---------------------------------------------------------------------------

def _resp(status_code: int, body: dict | None = None) -> httpx.Response:
    """Build a mock httpx.Response with a given status code and JSON body.

    REVUE-397 Finding #807015722: Non-2xx status codes must explicitly pass a body;
    they no longer default to a valid-pro response. This prevents silent mismatches
    where a future _resp(503) might accidentally claim the licence is valid.
    """
    if body is None:
        # Only default a valid body for 2xx success codes
        if 200 <= status_code < 300:
            body = {
                "valid": True,
                "tier": "pro",
                "agents_allowed": ["orchestrator", "code-quality-expert", "consolidator"],
                "reviews_left": None,
                "expires_at": "2027-01-01T00:00:00Z",
            }
        else:
            # Non-2xx codes must explicitly pass a body; default to None (error case)
            body = None
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    if body is not None:
        mock_resp.json.return_value = body
        mock_resp.text = json.dumps(body)
    else:
        # For error responses with no body, json() should raise or return empty
        mock_resp.json.return_value = {}
        mock_resp.text = ""
    return mock_resp


def _client_with_sequence(*outcomes) -> httpx.Client:
    """Mock httpx.Client whose .post() yields each outcome in order.

    Each outcome is either an httpx.Response (returned) or an Exception (raised).
    """
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = list(outcomes)
    return mock_client


class TestRetryTransientFailures:
    """REVUE-397 — `_call_api` retries transient failures before raising."""

    def setup_method(self):
        if CACHE_PATH.exists():
            CACHE_PATH.unlink()

    def test_connect_error_once_then_200_validates_after_one_retry(self):
        # Arrange — first attempt raises ConnectError, second returns a valid 200
        client = _client_with_sequence(
            httpx.ConnectError("Connection refused"),
            _resp(200),
        )

        # Act
        with patch("revue_core.core.license_validator.time.sleep"):
            info = validate("retry-key", _http_client=client)

        # Assert — succeeded without a spurious LicenseError
        assert info.valid is True
        assert info.tier == "pro"
        # Assert — the client was called exactly twice (one failure + one retry)
        assert client.post.call_count == 2

    def test_503_then_200_retries_then_succeeds(self):
        # Arrange — transient 503 then a valid 200
        client = _client_with_sequence(
            _resp(503, body={"error": "service unavailable"}),
            _resp(200),
        )

        # Act
        with patch("revue_core.core.license_validator.time.sleep"):
            info = validate("server-error-key", _http_client=client)

        # Assert — recovered after retrying the 5xx
        assert info.valid is True
        assert client.post.call_count == 2

    def test_persistent_connect_error_exhausts_retries_then_falls_through_to_cache(self):
        # Arrange — prime a fresh cache, then make every attempt fail transiently
        validate("persist-key", _http_client=_make_http_client())
        failing = MagicMock(spec=httpx.Client)
        failing.post.side_effect = httpx.ConnectError("Connection refused")

        # Act — exhausts retries, then existing 72h grace cache is used
        with patch("revue_core.core.license_validator.time.sleep"):
            info = validate("persist-key", _http_client=failing)

        # Assert — fell through to cache, unchanged grace-period behaviour
        assert info.valid is True
        assert info.tier == "pro"
        # Assert — exactly MAX_VALIDATION_ATTEMPTS calls were made
        from revue_core.core.license_validator import MAX_VALIDATION_ATTEMPTS
        assert failing.post.call_count == MAX_VALIDATION_ATTEMPTS

    def test_persistent_outage_with_no_cache_raises_after_exhausting_retries(self):
        # Arrange — every attempt times out, no cache present
        failing = MagicMock(spec=httpx.Client)
        failing.post.side_effect = httpx.TimeoutException("Timeout")

        # Act / Assert — falls through to the existing no-cache LicenseError
        from revue_core.core.license_validator import MAX_VALIDATION_ATTEMPTS
        with patch("revue_core.core.license_validator.time.sleep"):
            with pytest.raises(LicenseError, match="no local license cache"):
                validate("no-cache-key", _http_client=failing)
        assert failing.post.call_count == MAX_VALIDATION_ATTEMPTS

    def test_401_raises_immediately_with_no_retry(self):
        # Arrange — a single definitive 401 (invalid/revoked key)
        client = _client_with_sequence(_resp(401, body={"error": "revoked"}))

        # Act / Assert — LicenseError raised on the first attempt, never retried
        with patch("revue_core.core.license_validator.time.sleep"):
            with pytest.raises(LicenseError, match="invalid or has been revoked"):
                validate("revoked-key", _http_client=client)
        assert client.post.call_count == 1

    def test_other_4xx_not_retried_and_falls_through_to_cache(self):
        # Arrange — a 404 (non-retryable, not 5xx) on every call; prime cache first
        validate("client-4xx-key", _http_client=_make_http_client())
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _resp(404, body={"error": "not found"})

        # Act — 4xx is treated as unreachable but is NOT retried
        with patch("revue_core.core.license_validator.time.sleep"):
            info = validate("client-4xx-key", _http_client=client)

        # Assert — fell through to cache after a single (non-retried) attempt
        assert info.valid is True
        assert client.post.call_count == 1

    def test_backoff_is_bounded_and_sleeps_between_attempts(self):
        # Arrange — fail transiently on every attempt, no cache
        failing = MagicMock(spec=httpx.Client)
        failing.post.side_effect = httpx.ConnectError("refused")

        # Act
        from revue_core.core.license_validator import (
            MAX_VALIDATION_ATTEMPTS,
            RETRY_BACKOFF_BASE_SECONDS,
            RETRY_BACKOFF_CAP_SECONDS,
        )
        with patch("revue_core.core.license_validator.time.sleep") as mock_sleep:
            with pytest.raises(LicenseError):
                validate("backoff-key", _http_client=failing)

        # Assert — slept exactly once per inter-attempt gap (N-1 gaps for N attempts)
        assert mock_sleep.call_count == MAX_VALIDATION_ATTEMPTS - 1
        # Assert — every backoff value is bounded by the hardcoded cap
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        assert all(s <= RETRY_BACKOFF_CAP_SECONDS for s in slept)
        # Assert — first backoff equals the base (exponential schedule starts at base)
        assert slept[0] == RETRY_BACKOFF_BASE_SECONDS


def test_retry_budget_is_hardcoded_not_env_overridable() -> None:
    """AC3 — the retry budget is hardcoded, never sourced from the environment.

    Mirrors the VALIDATE_URL hardcoding premise: a CI-controllable attempt count
    would let an attacker stall or weaken validation. Source inspection pins that
    the retry constants are bare literals, not `os.environ` lookups.
    """
    import inspect

    import revue_core.core.license_validator as module
    source = inspect.getsource(module)

    # The retry constants exist and are module-level integers/floats, not env reads.
    assert isinstance(module.MAX_VALIDATION_ATTEMPTS, int)
    assert module.MAX_VALIDATION_ATTEMPTS >= 2
    assert isinstance(module.RETRY_BACKOFF_BASE_SECONDS, (int, float))
    assert isinstance(module.RETRY_BACKOFF_CAP_SECONDS, (int, float))
    # No env var named for retries/attempts/backoff anywhere in the module source.
    for forbidden in (
        "REVUE_MAX_VALIDATION_ATTEMPTS",
        "REVUE_RETRY_ATTEMPTS",
        "REVUE_RETRY_BACKOFF",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# REVUE-397: _compute_backoff pure function tests
# ---------------------------------------------------------------------------

class TestValidationResult:
    """REVUE-397 Finding #807015717 — _ValidationResult type and classification."""

    def test_validation_result_can_represent_success(self):
        """_ValidationResult can hold a successful API response."""
        from revue_core.core.license_validator import _ValidationResult

        body = {
            "valid": True,
            "tier": "pro",
            "agents_allowed": ["orchestrator"],
            "reviews_left": None,
            "expires_at": "2027-01-01T00:00:00Z",
        }
        result = _ValidationResult(status_code=200, body=body, error=None)
        assert result.status_code == 200
        assert result.body == body
        assert result.error is None
        assert result.is_success is True

    def test_validation_result_can_represent_network_error(self):
        """_ValidationResult can hold a network error (retryable)."""
        from revue_core.core.license_validator import _ValidationResult

        result = _ValidationResult(
            status_code=None, body=None, error="Connection refused"
        )
        assert result.status_code is None
        assert result.body is None
        assert result.error == "Connection refused"
        assert result.is_success is False

    def test_validation_result_can_represent_401(self):
        """_ValidationResult can hold a 401 response (not retryable)."""
        from revue_core.core.license_validator import _ValidationResult

        body = {"error": "Invalid key"}
        result = _ValidationResult(status_code=401, body=body, error=None)
        assert result.status_code == 401
        assert result.body == body
        assert result.error is None
        assert result.is_success is False

    def test_validation_result_can_represent_500(self):
        """_ValidationResult can hold a 500 response (retryable)."""
        from revue_core.core.license_validator import _ValidationResult

        body = {"error": "Internal server error"}
        result = _ValidationResult(status_code=500, body=body, error=None)
        assert result.status_code == 500
        assert result.body == body
        assert result.error is None
        assert result.is_success is False


class TestComputeBackoff:
    """REVUE-397 Finding #807015720 — Extract and test backoff computation."""

    def test_compute_backoff_attempt_1_returns_base(self):
        """First attempt uses base backoff directly (2^0 = 1 * base)."""
        from revue_core.core.license_validator import (
            _compute_backoff,
            RETRY_BACKOFF_BASE_SECONDS,
        )

        result = _compute_backoff(1)
        assert result == RETRY_BACKOFF_BASE_SECONDS

    def test_compute_backoff_attempt_2_doubles(self):
        """Second attempt doubles the base (2^1 = 2 * base)."""
        from revue_core.core.license_validator import (
            _compute_backoff,
            RETRY_BACKOFF_BASE_SECONDS,
        )

        result = _compute_backoff(2)
        assert result == RETRY_BACKOFF_BASE_SECONDS * 2

    def test_compute_backoff_attempt_3_quadruples(self):
        """Third attempt quadruples the base (2^2 = 4 * base)."""
        from revue_core.core.license_validator import (
            _compute_backoff,
            RETRY_BACKOFF_BASE_SECONDS,
        )

        result = _compute_backoff(3)
        assert result == RETRY_BACKOFF_BASE_SECONDS * 4

    def test_compute_backoff_respects_cap(self):
        """Backoff is capped at RETRY_BACKOFF_CAP_SECONDS."""
        from revue_core.core.license_validator import (
            _compute_backoff,
            RETRY_BACKOFF_CAP_SECONDS,
        )

        # Test with a very high attempt number (would exceed cap in uncapped formula)
        result = _compute_backoff(100)
        assert result == RETRY_BACKOFF_CAP_SECONDS
        assert result <= RETRY_BACKOFF_CAP_SECONDS

    def test_compute_backoff_cap_enforced_at_reasonable_attempt(self):
        """Cap is enforced before hitting very high attempt numbers."""
        from revue_core.core.license_validator import (
            _compute_backoff,
            RETRY_BACKOFF_BASE_SECONDS,
            RETRY_BACKOFF_CAP_SECONDS,
        )

        # With base=0.5 and cap=4.0, cap is hit at attempt 4 (0.5 * 2^3 = 4.0)
        # or attempt 5 if the logic is 2^(n-1)
        result = _compute_backoff(5)
        assert result == RETRY_BACKOFF_CAP_SECONDS

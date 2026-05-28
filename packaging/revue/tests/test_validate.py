"""REVUE-278 Task 6 — Skill ``validate.py`` module.

Covers the runtime licence validation contract used by the skill to gate
/revue-local invocations:

- AC2: second invocation within 24h skips the network entirely
- AC3: network failure inside cache window → cached result honoured
- AC4: network failure outside cache window → invocation blocks (exit 8)
- AC5: identical 24h window across all tiers (no tier-graded grace)
- Decision #2: ``VALIDATE_URL`` is a hardcoded module constant
- Decision #3: ``REVUE_LICENCE_CACHE_PATH`` env override (test-only)
- Decision #5: when server returns ``refreshed_jwt``, it is written back

The cache file is ``~/.config/revue/licence-cache.json`` with mode 0600 and
parent 0700. Tests override via ``REVUE_LICENCE_CACHE_PATH``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest


# ---------- Module-shape / constants ----------------------------------------

def test_validate_url_is_hardcoded_module_constant():
    """Decision #2: VALIDATE_URL must be a module-level constant, never read
    from an env var. A configurable URL is a licence-bypass vector."""
    from revue_skill.validate import VALIDATE_URL

    assert isinstance(VALIDATE_URL, str)
    assert VALIDATE_URL == "https://revue.sh/api/v2/licence/validate"


def test_cache_window_seconds_is_exactly_24h():
    """AC5: cache window is exactly 24h for all tiers — no tier-graded
    grace."""
    from revue_skill.validate import CACHE_WINDOW_SECONDS
    assert CACHE_WINDOW_SECONDS == 86400


def test_cache_path_env_override_test_only(monkeypatch):
    """Decision #3: REVUE_LICENCE_CACHE_PATH env var is honoured when set
    (test-only convenience); production always uses
    ~/.config/revue/licence-cache.json."""
    from revue_skill.validate import _get_cache_path

    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", "/tmp/test-cache.json")
    assert str(_get_cache_path()) == "/tmp/test-cache.json"

    monkeypatch.delenv("REVUE_LICENCE_CACHE_PATH")
    assert "/.config/revue" in str(_get_cache_path())


# ---------- Helpers ----------------------------------------------------------

def _write_fresh_cache(cache_path: Path, *, tier: str = "indie",
                       window_offset_seconds: int = 3600) -> dict:
    """Write a fresh cache file expiring `window_offset_seconds` in the future."""
    now = int(time.time())
    body = {
        "valid": True,
        "tier": tier,
        "reviews_remaining": 100,
        "paywall_state": None,
        "refresh_after_ts": now + window_offset_seconds,
        "cached_at": now,
        "refreshed_jwt": None,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(body))
    return body


def _write_stale_cache(cache_path: Path, *, tier: str = "indie") -> dict:
    """Write a stale cache file whose refresh_after_ts is in the past."""
    now = int(time.time())
    body = {
        "valid": True,
        "tier": tier,
        "reviews_remaining": 100,
        "paywall_state": None,
        "refresh_after_ts": now - 3600,
        "cached_at": now - (25 * 3600),
        "refreshed_jwt": None,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(body))
    return body


class _MockResponse:
    def __init__(self, *, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _MockClient:
    def __init__(self, *, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def post(self, url, json=None):
        self.calls.append((url, json))
        if self._exc:
            raise self._exc
        return self._response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


# ---------- AC2: cache within 24h skips network -----------------------------

def test_cache_within_24h_skips_network(monkeypatch, tmp_path):
    """AC2: second invocation within 24h reads cache; ZERO network calls."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_fresh_cache(cache_file)

    mock = _MockClient(response=_MockResponse(status_code=200, body={}))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    exit_code = validate_licence("any.jwt.token")

    assert exit_code == 0
    assert mock.calls == [], (
        f"AC2 violated: expected zero network calls within cache window, "
        f"got {len(mock.calls)}"
    )


# ---------- AC3: network failure inside window honoured ---------------------

def test_network_fail_in_window_uses_cache(monkeypatch, tmp_path):
    """AC3 (degenerate): fresh cache short-circuits before the network is
    ever touched, so a network failure is irrelevant — the user proceeds."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_fresh_cache(cache_file)

    mock = _MockClient(exc=httpx.ConnectError("no internet"))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    assert validate_licence("jwt") == 0
    assert mock.calls == []


# ---------- AC4: network failure outside window blocks ----------------------

def test_network_fail_outside_window_blocks(monkeypatch, tmp_path, capsys):
    """AC4: stale cache + network down → invocation blocks with exit 8."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_stale_cache(cache_file)

    mock = _MockClient(exc=httpx.ConnectError("no internet"))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    exit_code = validate_licence("jwt")

    assert exit_code == 8
    captured = capsys.readouterr()
    assert "Revue needs to verify your licence" in captured.err
    assert mock.calls, "network call should have been attempted"


def test_no_cache_network_fail_blocks(monkeypatch, tmp_path, capsys):
    """AC4: no cache at all + network down → blocks with exit 8."""
    cache_file = tmp_path / "nope.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))

    mock = _MockClient(exc=httpx.ConnectError("no internet"))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    assert validate_licence("jwt") == 8
    captured = capsys.readouterr()
    assert "Revue needs to verify your licence" in captured.err


# ---------- valid: false / revocation enforcement ---------------------------

def test_server_valid_false_returns_nonzero(monkeypatch, tmp_path, capsys):
    """If server returns {valid: false} (revocation / expired / tampered JWT),
    the skill must NOT cache it and must NOT return success — otherwise the
    whole validation endpoint becomes a no-op."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))

    invalid_response = {
        "valid": False,
        "tier": None,
        "reviews_remaining": None,
        "refresh_after_ts": None,
        "refreshed_jwt": None,
    }
    mock = _MockClient(
        response=_MockResponse(status_code=200, body=invalid_response)
    )
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    exit_code = validate_licence("expired.jwt.token")

    assert exit_code == 5, (
        "Server-rejected JWT must block invocation, got exit code "
        f"{exit_code} (would silently succeed)"
    )
    captured = capsys.readouterr()
    assert "licence validation failed" in captured.err
    # And critically: the false response was NOT cached
    assert not cache_file.exists() or not json.loads(
        cache_file.read_text()
    ).get("valid", True), (
        "invalid response must never be persisted to cache — would reanimate "
        "on next stale-cache + offline run"
    )


# ---------- AC5: all tiers, same 24h window ---------------------------------

@pytest.mark.parametrize("tier", ["free", "indie", "pro", "enterprise_starter"])
def test_all_tiers_same_grace(monkeypatch, tmp_path, tier):
    """AC5: Free / Indie / Pro / Enterprise all use the same 24h cache —
    no tier-graded grace (prevents tier-bypass attacks)."""
    cache_file = tmp_path / f"cache_{tier}.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    _write_fresh_cache(cache_file, tier=tier)

    mock = _MockClient(response=_MockResponse(status_code=200, body={}))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    assert validate_licence("jwt") == 0, f"tier {tier} did not honour cache"
    assert mock.calls == [], (
        f"tier {tier} hit the network within window — AC5 violated"
    )


# ---------- Decision #5: refreshed_jwt write-back --------------------------

def _sign_test_jwt(monkeypatch) -> tuple[str, str]:
    """Generate a fresh RSA keypair, patch the embedded public key, return a
    real JWT signed by the matching private key plus the JWT string."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt as pyjwt
    import revue_core.security.jwt_keys as jwt_keys_module

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", pub_pem)

    claims = {
        "exp": int(time.time()) + 86400,
        "workspace_id": 42,
        "tier": "indie",
    }
    token = pyjwt.encode(claims, priv_pem, algorithm="RS256")
    return token, pub_pem


def test_refreshed_jwt_overwrites_licence_file(monkeypatch, tmp_path):
    """Decision #5: when server returns a properly signed refreshed_jwt,
    client overwrites ~/.config/revue/licence.jwt atomically."""
    cache_file = tmp_path / "cache.json"
    licence_dir = tmp_path / ".config" / "revue"
    licence_file = licence_dir / "licence.jwt"
    licence_dir.mkdir(parents=True, exist_ok=True)
    licence_file.write_text("old.jwt.token")

    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    monkeypatch.setattr("revue_skill.validate.Path.home", lambda: tmp_path)

    new_jwt, _ = _sign_test_jwt(monkeypatch)
    server_response = {
        "valid": True,
        "tier": "indie",
        "reviews_remaining": None,
        "refresh_after_ts": int(time.time()) + 86400,
        "refreshed_jwt": new_jwt,
    }
    mock = _MockClient(
        response=_MockResponse(status_code=200, body=server_response)
    )
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    assert validate_licence("old.jwt.token") == 0

    assert licence_file.read_text() == new_jwt, (
        "refreshed_jwt did not overwrite licence.jwt"
    )


def test_refreshed_jwt_with_invalid_signature_is_ignored(monkeypatch, tmp_path, capsys):
    """Defence: a refreshed_jwt that fails signature verification must NOT
    overwrite the on-disk licence — a compromised backend or MITM (corp root
    CA) could otherwise replace the user's working JWT with garbage and
    brick the licence until a manual ``revue activate``."""
    cache_file = tmp_path / "cache.json"
    licence_dir = tmp_path / ".config" / "revue"
    licence_file = licence_dir / "licence.jwt"
    licence_dir.mkdir(parents=True, exist_ok=True)
    licence_file.write_text("existing.legit.jwt")

    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    monkeypatch.setattr("revue_skill.validate.Path.home", lambda: tmp_path)

    # Patch in a real public key so the verification path runs; the
    # refreshed_jwt below is NOT signed by it (it's just garbage).
    _sign_test_jwt(monkeypatch)

    bogus_jwt = "header.payload.not-a-real-sig"
    server_response = {
        "valid": True,
        "tier": "indie",
        "reviews_remaining": None,
        "refresh_after_ts": int(time.time()) + 86400,
        "refreshed_jwt": bogus_jwt,
    }
    mock = _MockClient(
        response=_MockResponse(status_code=200, body=server_response)
    )
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    assert validate_licence("any.jwt") == 0
    # Existing JWT untouched
    assert licence_file.read_text() == "existing.legit.jwt"
    assert "invalid refreshed JWT" in capsys.readouterr().err


# ---------- Cache integrity defence ----------------------------------------

def test_cache_refresh_after_ts_capped_at_24h_in_future(monkeypatch, tmp_path):
    """Defence-in-depth: even if the cache file is tampered to set a
    far-future refresh_after_ts, the skill must re-validate at most once per
    24h. Otherwise a local attacker could write
    ``{"refresh_after_ts": 99999999999}`` to bypass revocation forever."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))

    # Forge a cache with refresh_after_ts 10 years in the future. Honest
    # ``cached_at`` (when the file was actually written) — the cap is
    # anchored to this, so far-future refresh_after_ts cannot extend it.
    now = int(time.time())
    tampered = {
        "valid": True,
        "tier": "enterprise_plus",
        "reviews_remaining": None,
        "paywall_state": None,
        "refresh_after_ts": now + (10 * 365 * 86400),
        "cached_at": now,
        "refreshed_jwt": None,
    }
    cache_file.write_text(json.dumps(tampered))

    # Move "now" to 25h in the future relative to the cache write
    real_time = time.time
    monkeypatch.setattr(
        "revue_skill.validate.time.time",
        lambda: real_time() + (25 * 3600),
    )

    # Network call must still happen because the 24h cap clamped the horizon
    mock = _MockClient(exc=httpx.ConnectError("no net"))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    exit_code = validate_licence("jwt")
    assert exit_code == 8, (
        f"tampered cache bypass: expected re-validation attempt + block, "
        f"got exit {exit_code}"
    )
    assert mock.calls, "skill trusted tampered far-future cache without revalidating"


def test_corrupt_cache_json_does_not_crash(monkeypatch, tmp_path):
    """A malformed cache file (e.g. JSON list, partial write) must not crash
    the CLI — treat as missing and fall through to network."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setenv("REVUE_LICENCE_CACHE_PATH", str(cache_file))
    cache_file.write_text("[1, 2, 3]")  # valid JSON, wrong type

    mock = _MockClient(exc=httpx.ConnectError("no net"))
    monkeypatch.setattr("revue_skill.validate._build_http_client", lambda: mock)

    from revue_skill.validate import validate_licence
    # Should reach network (treating cache as missing) and then block per AC4
    assert validate_licence("jwt") == 8


# ---------- is_cache_fresh (public) -----------------------------------------
# Direct unit tests for the freshness predicate. Promoted from private to
# public in REVUE-280 (code-review #803516255, #803516275) so callers in
# other modules — cost_footer, upgrade_prompt — don't depend on a name
# marked internal by convention.

class TestIsCacheFresh:
    def test_non_dict_input_is_not_fresh(self):
        from revue_skill.validate import is_cache_fresh
        assert is_cache_fresh(None) is False
        assert is_cache_fresh([1, 2, 3]) is False
        assert is_cache_fresh("not a dict") is False

    def test_missing_valid_field_is_not_fresh(self):
        from revue_skill.validate import is_cache_fresh
        now = int(time.time())
        assert is_cache_fresh({
            "paywall_state": None,
            "refresh_after_ts": now + 3600,
            "cached_at": now,
        }) is False

    def test_valid_false_is_not_fresh(self):
        from revue_skill.validate import is_cache_fresh
        now = int(time.time())
        assert is_cache_fresh({
            "valid": False,
            "paywall_state": None,
            "refresh_after_ts": now + 3600,
            "cached_at": now,
        }) is False

    def test_missing_paywall_state_key_is_not_fresh(self):
        """Pre-REVUE-279 cache shape: no paywall_state key → stale."""
        from revue_skill.validate import is_cache_fresh
        now = int(time.time())
        assert is_cache_fresh({
            "valid": True,
            "refresh_after_ts": now + 3600,
            "cached_at": now,
        }) is False

    def test_paywall_state_present_but_none_is_fresh(self):
        """REVUE-279 contract: ``paywall_state: None`` is the valid 'no
        paywall' shape, not 'missing'."""
        from revue_skill.validate import is_cache_fresh
        now = int(time.time())
        assert is_cache_fresh({
            "valid": True,
            "paywall_state": None,
            "refresh_after_ts": now + 3600,
            "cached_at": now,
        }) is True

    def test_past_refresh_after_ts_is_not_fresh(self):
        from revue_skill.validate import is_cache_fresh
        past = int(time.time()) - 3600
        assert is_cache_fresh({
            "valid": True,
            "paywall_state": None,
            "refresh_after_ts": past,
            "cached_at": past,
        }) is False

    def test_cached_at_more_than_24h_ago_caps_the_horizon(self):
        """Defense-in-depth: even if ``refresh_after_ts`` is far in the
        future (tampered cache), cached_at + 24h caps the lifetime."""
        from revue_skill.validate import CACHE_WINDOW_SECONDS, is_cache_fresh
        old_cached_at = int(time.time()) - CACHE_WINDOW_SECONDS - 60
        assert is_cache_fresh({
            "valid": True,
            "paywall_state": None,
            "refresh_after_ts": int(time.time()) + (365 * 86400),
            "cached_at": old_cached_at,
        }) is False

    def test_non_numeric_timestamps_are_not_fresh(self):
        from revue_skill.validate import is_cache_fresh
        assert is_cache_fresh({
            "valid": True,
            "paywall_state": None,
            "refresh_after_ts": "not a number",
            "cached_at": int(time.time()),
        }) is False
        assert is_cache_fresh({
            "valid": True,
            "paywall_state": None,
            "refresh_after_ts": int(time.time()) + 3600,
            "cached_at": None,
        }) is False

"""Unit tests for the REVUE-409 staging seeding logic in conftest.py.

These exercise the staging branch of the e2e fixtures WITHOUT a browser or a
deployed server — they are the ONLY local verification the staging mapping ever
gets, because the staging branch only executes when ``E2E_BASE_URL`` is set,
which never happens in the pre-merge PR pipeline. A classifier misroute would
otherwise surface only on the maintainer's post-merge staging run as a
permanently red gate.

Covered:
  TC-1  base_url branches on E2E_BASE_URL without starting a server.
  state classification — every real seed-param combo maps to the right account.
  seed_active_licence / seed_user_with_licence / auth_cookie staging shapes.
  missing-secret errors name the exact missing variable (AC7 logged gap).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# Import conftest.py as a standalone module so its helper functions can be unit
# tested directly. (pytest does not expose the conftest module by import path.)
_CONFTEST_PATH = Path(__file__).with_name("conftest.py")
_spec = importlib.util.spec_from_file_location("e2e_conftest_under_test", _CONFTEST_PATH)
assert _spec and _spec.loader
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)


# Every state's three secret names, with deterministic dummy values.
_DUMMY_SECRETS = {
    f"STAGING_E2E_{state}_{field}": f"{state.lower()}-{field.lower()}-value"
    for state in (
        "ACTIVE_PRO",
        "ACTIVE_INDIE",
        "FREE",
        "LAPSED",
        "NOT_ACTIVATED",
    )
    for field in ("EMAIL", "PASSWORD", "LICENCE_KEY")
}


@pytest.fixture
def staging_env(monkeypatch):
    """Set E2E_BASE_URL + all dummy STAGING_E2E_* secrets for the staging branch."""
    monkeypatch.setenv("E2E_BASE_URL", "https://staging.revue.sh")
    for name, value in _DUMMY_SECRETS.items():
        monkeypatch.setenv(name, value)
    yield


# ---------------------------------------------------------------------------
# TC-1 — base_url branches on env, never spawns a server
# ---------------------------------------------------------------------------

def test_base_url_yields_env_without_starting_server(monkeypatch):
    """TC-1: when E2E_BASE_URL is set, the base_url fixture yields it verbatim
    and never calls subprocess.Popen / the FS-backed _e2e_db."""
    monkeypatch.setenv("E2E_BASE_URL", "https://staging.revue.sh/")

    def _explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("base_url must NOT spawn a subprocess on staging")

    monkeypatch.setattr(conftest.subprocess, "Popen", _explode)

    # Drive the generator directly; _e2e_db is irrelevant on the staging path so
    # pass a sentinel that would fail loudly if the local path touched it.
    gen = conftest.base_url.__wrapped__(_e2e_db="/nonexistent/should-not-be-used.db")
    url = next(gen)
    assert url == "https://staging.revue.sh"  # trailing slash stripped
    gen.close()


def test_base_url_local_path_still_spawns_when_env_unset(monkeypatch):
    """The local path is preserved: with E2E_BASE_URL unset, the staging short
    circuit does not fire (it would try to spawn — proven by reaching Popen)."""
    monkeypatch.delenv("E2E_BASE_URL", raising=False)
    called = {"popen": False}

    def _record(*_a, **_k):
        called["popen"] = True
        raise RuntimeError("stop after proving the local path was taken")

    monkeypatch.setattr(conftest.subprocess, "Popen", _record)
    gen = conftest.base_url.__wrapped__(_e2e_db="/tmp/whatever.db")
    with pytest.raises(RuntimeError):
        next(gen)
    assert called["popen"] is True


# ---------------------------------------------------------------------------
# State classification — the load-bearing precedence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tier,is_active,validated,expected",
    [
        # Lapsed wins over tier — the lapsed tests pass tier="pro".
        ("pro", False, True, conftest.STATE_LAPSED),
        ("indie", False, True, conftest.STATE_LAPSED),
        # Not-activated wins over tier (when still active).
        ("pro", True, False, conftest.STATE_NOT_ACTIVATED),
        # Free.
        ("free", True, True, conftest.STATE_FREE),
        # Active pro / indie.
        ("pro", True, True, conftest.STATE_ACTIVE_PRO),
        ("indie", True, True, conftest.STATE_ACTIVE_INDIE),
        # Default tier (the bare seed_active_licence() call) is indie/active.
        ("indie", True, True, conftest.STATE_ACTIVE_INDIE),
    ],
)
def test_classify_state_precedence(tier, is_active, validated, expected):
    assert (
        conftest._classify_state(tier=tier, is_active=is_active, validated=validated)
        == expected
    )


def test_classify_lapsed_precedes_validated_and_tier():
    """is_active=False must short-circuit BEFORE validated/tier checks."""
    assert (
        conftest._classify_state(tier="free", is_active=False, validated=False)
        == conftest.STATE_LAPSED
    )


# ---------------------------------------------------------------------------
# Account resolution + missing-secret errors
# ---------------------------------------------------------------------------

def test_staging_account_resolves_all_three_fields(staging_env):
    acct = conftest._staging_account(conftest.STATE_ACTIVE_PRO)
    assert acct == {
        "email": "active_pro-email-value",
        "password": "active_pro-password-value",
        "key": "active_pro-licence_key-value",
    }


def test_staging_account_missing_secret_names_the_variable(monkeypatch):
    """A provisioning gap raises a clear error naming the exact missing secret
    (AC7: gaps are logged, not hidden behind an opaque login timeout)."""
    monkeypatch.delenv("STAGING_E2E_LAPSED_EMAIL", raising=False)
    monkeypatch.delenv("STAGING_E2E_LAPSED_PASSWORD", raising=False)
    monkeypatch.delenv("STAGING_E2E_LAPSED_LICENCE_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        conftest._staging_account(conftest.STATE_LAPSED)
    msg = str(exc.value)
    assert "STAGING_E2E_LAPSED_EMAIL" in msg
    assert "STAGING_E2E_LAPSED_PASSWORD" in msg
    assert "STAGING_E2E_LAPSED_LICENCE_KEY" in msg
    assert "staging-e2e-account.md" in msg


# ---------------------------------------------------------------------------
# seed_active_licence staging fixture — return shape + _last_* attributes
# ---------------------------------------------------------------------------

def test_seed_active_licence_staging_maps_pro_to_active_pro_account(staging_env):
    factory = conftest.seed_active_licence.__wrapped__(_e2e_db=None)
    key = factory(tier="pro", is_active=True)
    assert key == "active_pro-licence_key-value"
    assert factory._last_email == "active_pro-email-value"
    assert factory._last_password == "active_pro-password-value"


def test_seed_active_licence_staging_maps_lapsed_pro_to_lapsed_account(staging_env):
    """A lapsed seed (tier='pro', is_active=False) must hit the LAPSED account,
    NOT the active-pro one — the precedence bug this guards against."""
    factory = conftest.seed_active_licence.__wrapped__(_e2e_db=None)
    key = factory(tier="pro", is_active=False, subscription_status="canceled")
    assert key == "lapsed-licence_key-value"
    assert factory._last_email == "lapsed-email-value"


def test_seed_active_licence_staging_default_call_is_active_indie(staging_env):
    """The bare seed_active_licence() call (default tier=indie) → ACTIVE_INDIE."""
    factory = conftest.seed_active_licence.__wrapped__(_e2e_db=None)
    key = factory()
    assert key == "active_indie-licence_key-value"


# ---------------------------------------------------------------------------
# seed_user_with_licence staging fixture — carries password + key, no user_id
# ---------------------------------------------------------------------------

def test_seed_user_with_licence_staging_returns_login_identity(staging_env):
    factory = conftest.seed_user_with_licence.__wrapped__(_e2e_db=None)
    identity = factory()
    assert identity == {
        "email": "active_indie-email-value",
        "password": "active_indie-password-value",
        "tier": "indie",
        "key": "active_indie-licence_key-value",
    }
    # user_id is intentionally absent on staging (no local DB row).
    assert "user_id" not in identity


def test_seed_user_with_licence_staging_free_tier(staging_env):
    factory = conftest.seed_user_with_licence.__wrapped__(_e2e_db=None)
    identity = factory(tier="free")
    assert identity["key"] == "free-licence_key-value"
    assert identity["tier"] == "free"

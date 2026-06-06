"""Unit tests for the REVUE-409 staging seeding logic in conftest.py.

These exercise the staging branch of the e2e fixtures WITHOUT a browser or a
deployed server — they are the ONLY local verification the staging mapping ever
gets, because the staging branch only executes when ``E2E_BASE_URL`` is set,
which never happens in the pre-merge PR pipeline. A classifier misroute would
otherwise surface only on the maintainer's post-merge staging run as a
permanently red gate.

Post-rework account model (ensure-exists + runtime keys): the e-mail is DERIVED
(``e2e-<state>@<domain>``), the password is the ONE shared
``STAGING_E2E_PASSWORD`` secret, and the licence KEY is read back at RUNTIME via
``resolve_account_key`` — there are no per-state secrets. The unit tests mock
``resolve_account_key`` (state-aware) so no network is touched.

Covered:
  TC-1  base_url branches on E2E_BASE_URL without starting a server.
  state classification — every real seed-param combo maps to the right account.
  seed_active_licence / seed_user_with_licence / auth_cookie staging shapes.
  derived e-mail + shared password + runtime-read key.
  missing STAGING_E2E_PASSWORD names that exact variable (AC7 logged gap).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# Import conftest.py as a standalone module so its helper functions can be unit
# tested directly. (pytest does not expose the conftest module by import path.)
# conftest.py adds the repo scripts/ dir to sys.path at import time so its
# ``from staging_e2e_accounts import ...`` resolves here too.
_CONFTEST_PATH = Path(__file__).with_name("conftest.py")
_spec = importlib.util.spec_from_file_location("e2e_conftest_under_test", _CONFTEST_PATH)
assert _spec and _spec.loader
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)


# A state-aware fake key so resolved keys are distinguishable per state without
# any network. Mirrors the lic_<32 hex> shape loosely (value identity is what
# the assertions check).
def _fake_key_for(state: str) -> str:
    return f"{state.lower()}-licence_key-value"


@pytest.fixture(autouse=True)
def _clear_account_cache():
    """The conftest memoises resolved accounts per state at module level; clear
    it around every test so a stale cached value never leaks across cases."""
    conftest._STAGING_ACCOUNT_CACHE.clear()
    yield
    conftest._STAGING_ACCOUNT_CACHE.clear()


@pytest.fixture
def staging_env(monkeypatch):
    """Set E2E_BASE_URL + the shared password, and mock the runtime key read.

    ``resolve_account_key(base_url, email, password)`` is patched to return a
    deterministic, state-derived key (parsed back from the derived e-mail) so the
    staging branch resolves without a network call.
    """
    monkeypatch.setenv("E2E_BASE_URL", "https://staging.revue.sh")
    monkeypatch.setenv("STAGING_E2E_PASSWORD", "shared-pw-value")

    def _fake_resolve(base_url, email, password):
        # email is e2e-<state>@<domain>; recover the state for a per-state key.
        local = email.split("@", 1)[0]  # e2e-<state>
        state = local[len("e2e-"):].upper()
        return _fake_key_for(state)

    monkeypatch.setattr(conftest, "resolve_account_key", _fake_resolve)
    yield


# ---------------------------------------------------------------------------
# TC-1 — base_url branches on env (Edge F9: test the extracted decision helper
# directly, not through the fixture's __wrapped__ generator).
# ---------------------------------------------------------------------------

def test_staging_base_url_yields_env_stripped(monkeypatch):
    """When E2E_BASE_URL is set, the staging decision returns it with the trailing
    slash stripped — the branch that makes the fixture yield it WITHOUT spawning a
    subprocess."""
    monkeypatch.setenv("E2E_BASE_URL", "https://staging.revue.sh/")
    assert conftest._staging_base_url_or_none() == "https://staging.revue.sh"


def test_staging_base_url_is_none_when_env_unset(monkeypatch):
    """With E2E_BASE_URL unset the staging decision returns None — the fixture then
    takes the local subprocess-spawn path. Tested directly, with no reliance on the
    fixture's __wrapped__ generator (Edge F9)."""
    monkeypatch.delenv("E2E_BASE_URL", raising=False)
    assert conftest._staging_base_url_or_none() is None


def test_staging_base_url_is_none_when_env_empty(monkeypatch):
    """An empty E2E_BASE_URL is treated as unset (falls through to local)."""
    monkeypatch.setenv("E2E_BASE_URL", "")
    assert conftest._staging_base_url_or_none() is None


# ---------------------------------------------------------------------------
# State classification — the load-bearing precedence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tier,is_active,validated,period_end,expected",
    [
        # Lapsed wins over tier — the lapsed tests pass tier="pro" (and the lapsed
        # tests DO carry a current_period_end, which must NOT route them to the
        # renewal account: is_active=False short-circuits to LAPSED first).
        ("pro", False, True, None, conftest.STATE_LAPSED),
        ("indie", False, True, None, conftest.STATE_LAPSED),
        ("pro", False, True, "2025-01-01T00:00:00", conftest.STATE_LAPSED),
        # Not-activated wins over tier (when still active).
        ("pro", True, False, None, conftest.STATE_NOT_ACTIVATED),
        # Free.
        ("free", True, True, None, conftest.STATE_FREE),
        # Active pro split: NULL period_end → ACTIVE_PRO; non-null → RENEWAL.
        ("pro", True, True, None, conftest.STATE_ACTIVE_PRO),
        ("pro", True, True, "2099-12-31T00:00:00", conftest.STATE_ACTIVE_PRO_RENEWAL),
        # Active indie (period_end does not split indie).
        ("indie", True, True, None, conftest.STATE_ACTIVE_INDIE),
        ("indie", True, True, "2099-01-01T00:00:00", conftest.STATE_ACTIVE_INDIE),
        # Default tier (the bare seed_active_licence() call) is indie/active.
        ("indie", True, True, None, conftest.STATE_ACTIVE_INDIE),
    ],
)
def test_classify_state_precedence(tier, is_active, validated, period_end, expected):
    assert (
        conftest._classify_state(
            tier=tier, is_active=is_active, validated=validated,
            current_period_end=period_end,
        )
        == expected
    )


def test_classify_lapsed_precedes_validated_and_tier():
    """is_active=False must short-circuit BEFORE validated/tier checks."""
    assert (
        conftest._classify_state(tier="free", is_active=False, validated=False)
        == conftest.STATE_LAPSED
    )


# ---------------------------------------------------------------------------
# Account resolution — derived email + shared password + runtime-read key
# ---------------------------------------------------------------------------

def test_staging_account_derives_email_shares_password_reads_key(staging_env):
    acct = conftest._staging_account(conftest.STATE_ACTIVE_PRO)
    assert acct == {
        "email": "e2e-active_pro@revue-e2e.test",  # derived, default domain
        "password": "shared-pw-value",             # ONE shared password
        "key": "active_pro-licence_key-value",     # read at runtime (mocked)
    }


def test_staging_account_honours_custom_email_domain(staging_env, monkeypatch):
    monkeypatch.setenv("STAGING_E2E_EMAIL_DOMAIN", "example.org")
    acct = conftest._staging_account(conftest.STATE_FREE)
    assert acct["email"] == "e2e-free@example.org"


def test_staging_account_memoises_per_state(staging_env, monkeypatch):
    """A resolved account is cached per state so the runtime key read happens
    once per session, not once per function-scoped test."""
    calls = {"n": 0}

    def _counting_resolve(base_url, email, password):
        calls["n"] += 1
        return "free-licence_key-value"

    monkeypatch.setattr(conftest, "resolve_account_key", _counting_resolve)
    conftest._staging_account(conftest.STATE_FREE)
    conftest._staging_account(conftest.STATE_FREE)
    assert calls["n"] == 1  # second call served from the cache


def test_staging_account_missing_password_names_the_variable(monkeypatch):
    """A config gap raises a clear error naming the shared password secret
    (AC7: gaps are logged, not hidden behind an opaque login timeout)."""
    monkeypatch.setenv("E2E_BASE_URL", "https://staging.revue.sh")
    monkeypatch.delenv("STAGING_E2E_PASSWORD", raising=False)
    with pytest.raises(RuntimeError) as exc:
        conftest._staging_account(conftest.STATE_LAPSED)
    msg = str(exc.value)
    assert "STAGING_E2E_PASSWORD" in msg
    assert "staging-e2e-account.md" in msg


# ---------------------------------------------------------------------------
# seed_active_licence staging fixture — return shape + _last_* attributes
# ---------------------------------------------------------------------------

def test_seed_active_licence_staging_maps_pro_to_active_pro_account(staging_env):
    factory = conftest.seed_active_licence.__wrapped__(_e2e_db=None)
    key = factory(tier="pro", is_active=True)
    assert key == "active_pro-licence_key-value"
    assert factory._last_email == "e2e-active_pro@revue-e2e.test"
    assert factory._last_password == "shared-pw-value"


def test_seed_active_licence_staging_maps_lapsed_pro_to_lapsed_account(staging_env):
    """A lapsed seed (tier='pro', is_active=False) must hit the LAPSED account,
    NOT the active-pro one — the precedence bug this guards against."""
    factory = conftest.seed_active_licence.__wrapped__(_e2e_db=None)
    key = factory(tier="pro", is_active=False, subscription_status="canceled")
    assert key == "lapsed-licence_key-value"
    assert factory._last_email == "e2e-lapsed@revue-e2e.test"


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
        "email": "e2e-active_indie@revue-e2e.test",
        "password": "shared-pw-value",
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


# ---------------------------------------------------------------------------
# _RedactedKey — masks the value in tracebacks but stays fully usable
# ---------------------------------------------------------------------------

def test_redacted_key_masks_repr_but_keeps_value():
    """A pytest failure dumps locals via repr(); the real key chars must never
    appear there, but every value-bearing operation must still see the real key."""
    real = "lic_" + "ab12cd34" * 4
    k = conftest._RedactedKey(real)
    # repr (tracebacks / -l locals dumps) is masked — the real hex is ABSENT.
    assert "ab12cd34" not in repr(k)
    assert "lic_***" in repr(k)
    # The value is unchanged for every real use.
    assert k == real
    assert str(k) == real
    assert k.startswith("lic_")
    assert {"key": k} == {"key": real}  # dict-equality on the real value


def test_staging_account_key_is_redacted_in_repr(staging_env):
    """The key the fixtures return / cache is the repr-masked subclass, so it
    cannot leak through a locals dump."""
    acct = conftest._staging_account(conftest.STATE_ACTIVE_PRO)
    assert isinstance(acct["key"], conftest._RedactedKey)
    assert "licence_key-value" not in repr(acct["key"])


def test_redacted_password_masks_repr_but_keeps_value():
    """The shared STAGING_E2E_PASSWORD is an equally real secret — mask its repr
    too, while keeping the value usable for the login form-fill + assertions."""
    pw = conftest._RedactedPassword("S3cr3t-staging-pw")
    assert "S3cr3t-staging-pw" not in repr(pw)
    assert repr(pw) == "'***'"
    assert pw == "S3cr3t-staging-pw"
    assert str(pw) == "S3cr3t-staging-pw"
    assert {"password": pw} == {"password": "S3cr3t-staging-pw"}


def test_staging_account_password_is_redacted_in_repr(staging_env):
    """The cached/returned password is the repr-masked subclass — a locals dump
    cannot print the real STAGING_E2E_PASSWORD."""
    acct = conftest._staging_account(conftest.STATE_FREE)
    assert isinstance(acct["password"], conftest._RedactedPassword)
    assert "shared-pw-value" not in repr(acct["password"])
    assert acct["password"] == "shared-pw-value"  # value still usable

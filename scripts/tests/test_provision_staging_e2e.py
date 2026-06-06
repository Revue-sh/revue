"""Unit tests for the PURE plan-builder of scripts/provision_staging_e2e.py.

Only the no-I/O, no-secret planner is tested here (live Stripe/staging execution
is exercised by the maintainer). These guard the load-bearing decisions:
  * the action sequence per state,
  * that LAPSED induces past_due (NOT cancel — cancel maps to free in billing.py),
  * that NOT_ACTIVATED skips the activate round-trip,
  * that the dry-run render leaks no secret values,
  * env validation fails fast for a live run and is lenient for dry-run.

This file lives under scripts/ (provisioning tooling), OUT of the e2e suite.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parent.parent / "provision_staging_e2e.py"
_spec = importlib.util.spec_from_file_location("provision_staging_e2e", _MOD_PATH)
assert _spec and _spec.loader
prov = importlib.util.module_from_spec(_spec)
# Register before exec: dataclasses with `from __future__ import annotations`
# resolve field types via sys.modules[cls.__module__] during class creation.
sys.modules["provision_staging_e2e"] = prov
_spec.loader.exec_module(prov)


def _dry_cfg(**over) -> "prov.Config":
    env = {"STAGING_BASE_URL": "https://staging.revue.sh"}
    env.update(over)
    return prov.Config.from_env(env, require_live=False)


def _action_kinds(plan) -> list[str]:
    return [a.kind for a in plan.actions]


# ---------------------------------------------------------------------------
# Per-state action sequences
# ---------------------------------------------------------------------------

def test_free_signs_up_then_activates_no_stripe():
    plan = prov.build_state_plan(prov.STATE_FREE, _dry_cfg())
    assert _action_kinds(plan) == ["signup", "activate_roundtrip"]


def test_active_indie_subscribes_after_activate():
    plan = prov.build_state_plan(prov.STATE_ACTIVE_INDIE, _dry_cfg())
    assert _action_kinds(plan) == ["signup", "activate_roundtrip", "stripe_subscribe"]


def test_active_pro_subscribes_after_activate():
    plan = prov.build_state_plan(prov.STATE_ACTIVE_PRO, _dry_cfg())
    assert _action_kinds(plan) == ["signup", "activate_roundtrip", "stripe_subscribe"]


def test_lapsed_induces_past_due_not_cancel():
    """LAPSED must subscribe THEN lapse via past_due — never cancel (cancel maps
    to FREE in billing.py). The lapse action's detail records that semantics."""
    plan = prov.build_state_plan(prov.STATE_LAPSED, _dry_cfg())
    assert _action_kinds(plan) == [
        "signup",
        "activate_roundtrip",
        "stripe_subscribe",
        "stripe_lapse",
    ]
    lapse = [a for a in plan.actions if a.kind == "stripe_lapse"][0]
    assert "past_due" in lapse.detail
    assert "cancel" in lapse.detail.lower()  # explicitly warns cancel != lapsed


def test_not_activated_signs_up_only_no_activate():
    """NOT_ACTIVATED must NOT run the activate round-trip — the key stays
    never-validated so the state resolves to not-activated."""
    plan = prov.build_state_plan(prov.STATE_NOT_ACTIVATED, _dry_cfg())
    kinds = _action_kinds(plan)
    assert "activate_roundtrip" not in kinds
    assert kinds[0] == "signup"


# ---------------------------------------------------------------------------
# Secrets + email derivation
# ---------------------------------------------------------------------------

def test_state_plan_lists_the_three_secret_names():
    plan = prov.build_state_plan(prov.STATE_ACTIVE_PRO, _dry_cfg())
    assert plan.secrets == (
        "STAGING_E2E_ACTIVE_PRO_EMAIL",
        "STAGING_E2E_ACTIVE_PRO_PASSWORD",
        "STAGING_E2E_ACTIVE_PRO_LICENCE_KEY",
    )


def test_email_is_stable_and_state_scoped():
    cfg = _dry_cfg(STAGING_E2E_EMAIL_DOMAIN="revue-e2e.test")
    assert cfg.email_for(prov.STATE_LAPSED) == "e2e-lapsed@revue-e2e.test"
    # Stable across calls (idempotency relies on this).
    assert cfg.email_for(prov.STATE_LAPSED) == cfg.email_for(prov.STATE_LAPSED)


def test_required_states_are_the_four_account_states():
    assert prov.REQUIRED_STATES == [
        "ACTIVE_PRO",
        "ACTIVE_INDIE",
        "FREE",
        "LAPSED",
    ]
    assert prov.OPTIONAL_STATES == ["NOT_ACTIVATED"]


# ---------------------------------------------------------------------------
# Dry-run render leaks no secrets
# ---------------------------------------------------------------------------

def test_render_plan_contains_no_secret_values():
    cfg = _dry_cfg(
        STRIPE_SECRET_KEY="sk_test_SHOULD_NOT_APPEAR",
        STAGING_E2E_PASSWORD="SuperSecretPw_SHOULD_NOT_APPEAR",
        STRIPE_PRICE_PRO_MONTHLY="price_SHOULD_NOT_APPEAR",
    )
    plans = prov.build_provision_plan(prov.REQUIRED_STATES, cfg)
    out = prov.render_plan(plans)
    assert "sk_test_SHOULD_NOT_APPEAR" not in out
    assert "SuperSecretPw_SHOULD_NOT_APPEAR" not in out
    assert "price_SHOULD_NOT_APPEAR" not in out
    # It DOES name the deviation and the secret NAMES (names are not secrets).
    assert "past_due" in out
    assert "STAGING_E2E_LAPSED_LICENCE_KEY" in out


# ---------------------------------------------------------------------------
# Env validation
# ---------------------------------------------------------------------------

def test_live_validation_fails_fast_naming_missing_vars():
    env = {"STAGING_BASE_URL": "https://staging.revue.sh"}  # no stripe/price/pw
    with pytest.raises(SystemExit) as exc:
        prov.Config.from_env(env, require_live=True)
    msg = str(exc.value)
    for var in (
        "STRIPE_SECRET_KEY",
        "STRIPE_PRICE_INDIE_MONTHLY",
        "STRIPE_PRICE_PRO_MONTHLY",
        "STAGING_E2E_PASSWORD",
    ):
        assert var in msg


def test_live_validation_refuses_live_stripe_key():
    env = {
        "STRIPE_SECRET_KEY": "sk_live_DANGER",
        "STRIPE_PRICE_INDIE_MONTHLY": "price_a",
        "STRIPE_PRICE_PRO_MONTHLY": "price_b",
        "STAGING_E2E_PASSWORD": "pw",
    }
    with pytest.raises(SystemExit) as exc:
        prov.Config.from_env(env, require_live=True)
    assert "LIVE key" in str(exc.value)


def test_dry_run_config_does_not_require_secrets():
    # Should not raise even with no secret env present.
    cfg = prov.Config.from_env({}, require_live=False)
    assert cfg.base_url == "https://staging.revue.sh"
    assert cfg.stripe_secret_key is None


# ---------------------------------------------------------------------------
# State selection (CLI helper)
# ---------------------------------------------------------------------------

def test_selected_states_defaults_to_required_only():
    import argparse

    ns = argparse.Namespace(state=None, include_optional=False, dry_run=True)
    assert prov._selected_states(ns) == prov.REQUIRED_STATES


def test_selected_states_include_optional_adds_not_activated():
    import argparse

    ns = argparse.Namespace(state=None, include_optional=True, dry_run=True)
    assert prov._selected_states(ns) == prov.REQUIRED_STATES + prov.OPTIONAL_STATES


def test_selected_states_explicit_dedups_preserving_order():
    import argparse

    ns = argparse.Namespace(
        state=["ACTIVE_PRO", "FREE", "ACTIVE_PRO"], include_optional=False, dry_run=True
    )
    assert prov._selected_states(ns) == ["ACTIVE_PRO", "FREE"]

"""Unit tests for scripts/provision_staging_e2e.py (no network, no live Stripe).

These guard the load-bearing decisions of the provisioner:
  * the action sequence per state (now ``emit_webhooks`` for paid states),
  * that LAPSED is past_due (NOT cancel — cancel maps to free in billing.py),
  * that NOT_ACTIVATED skips the activate round-trip,
  * that the dry-run render leaks no secret values,
  * env validation fails fast for a live run and is lenient for dry-run (and that
    NO Stripe API key is required — the paid states are signed synthetic webhooks),
  * the CSRF-aware signup/login + /onboarding user_id reads (httpx mocked),
  * the verify-then-exit poll convergence + loud timeout.

The signed-webhook signing/emit/event-builder helpers live in
``staging_e2e_accounts`` and are unit-tested in
``scripts/tests/test_staging_e2e_accounts.py``. This file lives under scripts/
(provisioning tooling), OUT of the e2e suite.
"""
from __future__ import annotations

import pytest

# scripts/ is on sys.path via scripts/tests/conftest.py, so the module (and its
# sibling import of staging_e2e_accounts) resolve as a normal import.
import provision_staging_e2e as prov


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


def test_active_indie_emits_webhooks_after_activate():
    plan = prov.build_state_plan(prov.STATE_ACTIVE_INDIE, _dry_cfg())
    assert _action_kinds(plan) == ["signup", "activate_roundtrip", "emit_webhooks"]


def test_active_pro_emits_webhooks_after_activate():
    plan = prov.build_state_plan(prov.STATE_ACTIVE_PRO, _dry_cfg())
    assert _action_kinds(plan) == ["signup", "activate_roundtrip", "emit_webhooks"]


def test_active_pro_renewal_emits_webhooks_after_activate():
    plan = prov.build_state_plan(prov.STATE_ACTIVE_PRO_RENEWAL, _dry_cfg())
    assert _action_kinds(plan) == ["signup", "activate_roundtrip", "emit_webhooks"]
    detail = [a for a in plan.actions if a.kind == "emit_webhooks"][0].detail
    assert "2099-12-31" in detail  # the fixed renewal date the page asserts


def test_lapsed_skips_activate_and_emits_past_due_webhooks():
    """LAPSED must NOT run the activate round-trip (it renders from is_active=False
    alone, and its key is unreadable once past_due lands — reading it would break
    the idempotent re-run). It signs up then emits the active+past_due webhook
    sequence; the detail records the past_due-not-cancel semantics (cancel maps to
    FREE in billing.py)."""
    plan = prov.build_state_plan(prov.STATE_LAPSED, _dry_cfg())
    kinds = _action_kinds(plan)
    assert "activate_roundtrip" not in kinds
    assert kinds == ["signup", "none", "emit_webhooks"]
    detail = [a for a in plan.actions if a.kind == "emit_webhooks"][0].detail
    assert "past_due" in detail
    assert "cancel" in detail.lower()  # explicitly warns cancel != lapsed


def test_not_activated_signs_up_only_no_activate():
    """NOT_ACTIVATED must NOT run the activate round-trip — the key stays
    never-validated so the state resolves to not-activated."""
    plan = prov.build_state_plan(prov.STATE_NOT_ACTIVATED, _dry_cfg())
    kinds = _action_kinds(plan)
    assert "activate_roundtrip" not in kinds
    assert kinds[0] == "signup"


# ---------------------------------------------------------------------------
# Email derivation (no per-state secrets — keys are read at runtime now)
# ---------------------------------------------------------------------------

def test_state_plan_carries_no_per_state_secret_names():
    """Post-rework: the plan ensures the account exists; it does NOT emit
    STAGING_E2E_<STATE>_{EMAIL,PASSWORD,LICENCE_KEY} secrets (those are gone —
    one shared password + derived email + runtime-read key)."""
    plan = prov.build_state_plan(prov.STATE_ACTIVE_PRO, _dry_cfg())
    assert not hasattr(plan, "secrets")


def test_email_is_stable_and_state_scoped():
    cfg = _dry_cfg(STAGING_E2E_EMAIL_DOMAIN="revue-e2e.test")
    assert cfg.email_for(prov.STATE_LAPSED) == "e2e-lapsed@revue-e2e.test"
    # Stable across calls (idempotency relies on this).
    assert cfg.email_for(prov.STATE_LAPSED) == cfg.email_for(prov.STATE_LAPSED)


def test_required_states_are_the_five_account_states():
    assert prov.REQUIRED_STATES == [
        "ACTIVE_PRO",
        "ACTIVE_PRO_RENEWAL",
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
        STRIPE_WEBHOOK_SECRET="whsec_SHOULD_NOT_APPEAR",
        STAGING_E2E_PASSWORD="SuperSecretPw_SHOULD_NOT_APPEAR",
        STRIPE_PRICE_PRO_MONTHLY="price_SHOULD_NOT_APPEAR",
    )
    plans = prov.build_provision_plan(prov.REQUIRED_STATES, cfg)
    out = prov.render_plan(plans)
    assert "whsec_SHOULD_NOT_APPEAR" not in out
    assert "SuperSecretPw_SHOULD_NOT_APPEAR" not in out
    assert "price_SHOULD_NOT_APPEAR" not in out
    # It DOES name the LAPSED past_due semantics (not a secret value).
    assert "past_due" in out
    # No per-state _LICENCE_KEY secret name appears — those secrets are gone.
    assert "STAGING_E2E_LAPSED_LICENCE_KEY" not in out
    assert "LICENCE_KEY" not in out


# ---------------------------------------------------------------------------
# Env validation
# ---------------------------------------------------------------------------

def test_live_validation_fails_fast_naming_missing_vars():
    env = {"STAGING_BASE_URL": "https://staging.revue.sh"}  # no webhook/price/pw
    with pytest.raises(SystemExit) as exc:
        prov.Config.from_env(env, require_live=True)
    msg = str(exc.value)
    for var in (
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICE_INDIE_MONTHLY",
        "STRIPE_PRICE_PRO_MONTHLY",
        "STAGING_E2E_PASSWORD",
    ):
        assert var in msg
    # No Stripe API key is required any more — it must not appear in the guard.
    assert "STRIPE_SECRET_KEY" not in msg


def test_live_validation_accepts_complete_env():
    """With the webhook secret + price ids + password present, a live Config
    builds without raising — there is NO Stripe-API-key guard any more (we never
    call the Stripe API; the only Stripe secret is the webhook signing secret)."""
    env = {
        "STRIPE_WEBHOOK_SECRET": "whsec_test",
        "STRIPE_PRICE_INDIE_MONTHLY": "price_a",
        "STRIPE_PRICE_PRO_MONTHLY": "price_b",
        "STAGING_E2E_PASSWORD": "pw",
    }
    cfg = prov.Config.from_env(env, require_live=True)  # must not raise
    assert cfg.webhook_secret == "whsec_test"
    assert not hasattr(cfg, "stripe_secret_key")


def test_dry_run_config_does_not_require_secrets():
    # Should not raise even with no secret env present.
    cfg = prov.Config.from_env({}, require_live=False)
    assert cfg.base_url == "https://staging.revue.sh"
    assert cfg.webhook_secret is None


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


# ---------------------------------------------------------------------------
# HTTP path — CSRF-aware signup/login + user_id resolution (httpx mocked)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeHttpClient:
    """Records posts/gets and serves canned pages keyed by path."""

    def __init__(self, *, pages: dict) -> None:
        self._pages = pages
        self.posts: list[tuple] = []
        self.gets: list[str] = []

    def post(self, path, data=None, **_kw):
        self.posts.append((path, dict(data or {})))
        return _FakeResp(self._pages.get(("POST", path), ""), 200)

    def get(self, path, **_kw):
        self.gets.append(path)
        return _FakeResp(self._pages.get(path, ""))


_CSRF = "signed.csrf.token"
_SIGNUP_FORM = (
    f'<input type="hidden" name="csrf_token" value="{_CSRF}">'
    '<input name="email">'
)
_KEY = "lic_" + "0a1b2c3d" * 4


def test_signup_or_login_fresh_signup_submits_csrf_then_key_is_readable():
    """Fresh signup: GET /signup renders the csrf field, the POST echoes it, the
    redirect lands on /onboarding which carries the key.

    _signup_or_login only ESTABLISHES the session (returns None now) — the key read
    is decoupled into _read_licence_key, called separately by the activate-needing
    states so LAPSED never reads a key it cannot satisfy."""
    client = _FakeHttpClient(pages={
        "/signup": _SIGNUP_FORM,
        ("POST", "/signup"): f"<code>revue activate {_KEY}</code>",  # redirected onboarding
        "/onboarding": f"<code>revue activate {_KEY}</code>",
    })
    assert prov._signup_or_login(
        client, "e2e-free@revue-e2e.test", "pw", log=lambda *_: None
    ) is None
    signup_posts = [d for p, d in client.posts if p == "/signup"]
    assert signup_posts and signup_posts[0]["csrf_token"] == _CSRF
    # The key is readable from the established session via the decoupled reader.
    assert prov._read_licence_key(client, log=lambda *_: None) == _KEY


def test_signup_or_login_duplicate_signup_falls_back_to_login():
    """Duplicate signup returns HTTP 200 with the 'already exists' phrase (NOT
    400/409 — verified against auth_routes.py). Detection is by body text, and the
    fallback POSTs a CSRF-tokened /login. _signup_or_login returns None (session
    only); the key remains readable via the decoupled _read_licence_key."""
    client = _FakeHttpClient(pages={
        "/signup": _SIGNUP_FORM,
        ("POST", "/signup"): "An account with this email already exists.",
        "/login": _SIGNUP_FORM,  # carries a csrf field too
        "/onboarding": f"key {_KEY}",
    })
    assert prov._signup_or_login(
        client, "e2e-free@revue-e2e.test", "pw", log=lambda *_: None
    ) is None
    # A /login POST happened, carrying the csrf token.
    login_posts = [d for p, d in client.posts if p == "/login"]
    assert login_posts and login_posts[0]["csrf_token"] == _CSRF
    # Key still readable post-login via the decoupled reader.
    assert prov._read_licence_key(client, log=lambda *_: None) == _KEY


def test_resolve_user_id_reads_onboarding_marker():
    client = _FakeHttpClient(pages={
        "/onboarding": '<span data-user-id="4242" hidden></span>',
    })
    assert prov._resolve_user_id(client, log=lambda *_: None) == "4242"
    assert client.gets == ["/onboarding"]  # only the onboarding page is fetched


def test_resolve_user_id_raises_when_marker_absent():
    client = _FakeHttpClient(pages={"/onboarding": "<p>no marker here</p>"})
    with pytest.raises(RuntimeError) as exc:
        prov._resolve_user_id(client, log=lambda *_: None)
    assert "data-user-id" in str(exc.value)


# ---------------------------------------------------------------------------
# Verify-then-exit poll — converges on the expected state, times out loudly
# ---------------------------------------------------------------------------

def test_verify_state_returns_when_plan_shows_expected_cue():
    client = _FakeHttpClient(pages={
        "/account/plan": "Your plan: Pro — Licence active ✓",
    })
    prov._verify_state(client, prov.STATE_ACTIVE_PRO, log=lambda *_: None,
                       timeout=5.0, interval=0.0)


def test_verify_state_active_pro_does_not_match_indie_page():
    """Tier match is case-sensitive on the exact badge: an Indie page must NOT
    satisfy ACTIVE_PRO (guards the Pro/Indie mixup the loose substring allowed)."""
    client = _FakeHttpClient(pages={
        "/account/plan": "Your plan: Indie — Licence active ✓",
    })
    with pytest.raises(RuntimeError):
        prov._verify_state(client, prov.STATE_ACTIVE_PRO, log=lambda *_: None,
                           timeout=0.05, interval=0.0)


def test_verify_state_pro_not_fooled_by_lowercase_substring():
    """A page containing 'profile'/'approve' but not the 'Licence active' cue must
    NOT converge ACTIVE_PRO — the cue, not an accidental 'pro' substring, gates."""
    client = _FakeHttpClient(pages={
        "/account/plan": "Manage your profile and approve requests",
    })
    with pytest.raises(RuntimeError):
        prov._verify_state(client, prov.STATE_ACTIVE_PRO, log=lambda *_: None,
                           timeout=0.05, interval=0.0)


def test_verify_state_free_requires_no_command_box():
    client = _FakeHttpClient(pages={"/account/plan": "Upgrade to a paid plan"})
    prov._verify_state(client, prov.STATE_FREE, log=lambda *_: None,
                       timeout=5.0, interval=0.0)


def test_verify_state_times_out_naming_the_state():
    client = _FakeHttpClient(pages={"/account/plan": "Free — Upgrade"})
    # ACTIVE_PRO never converges against a free page → times out, names the state.
    with pytest.raises(RuntimeError) as exc:
        prov._verify_state(client, prov.STATE_ACTIVE_PRO, log=lambda *_: None,
                           timeout=0.05, interval=0.0)
    assert "ACTIVE_PRO" in str(exc.value)


# ---------------------------------------------------------------------------
# _verify_timeout_message (Edge F3/F6) — names the activate/JWT/429 cause only for
# the validation-dependent states; LAPSED gets the webhook-only message.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "state",
    [prov.STATE_FREE, prov.STATE_ACTIVE_PRO, prov.STATE_ACTIVE_PRO_RENEWAL,
     prov.STATE_ACTIVE_INDIE],
)
def test_verify_timeout_message_names_activate_cause_for_validated_states(state):
    msg = prov._verify_timeout_message(state, 60.0)
    assert state in msg
    assert "429" in msg
    assert "JWT" in msg or "activate" in msg.lower()


def test_verify_timeout_message_lapsed_is_webhook_only():
    """LAPSED renders from is_active=False alone (no validation), so its timeout
    message must NOT blame activate/JWT/429 — only webhook/linkage."""
    msg = prov._verify_timeout_message(prov.STATE_LAPSED, 60.0)
    assert prov.STATE_LAPSED in msg
    assert "429" not in msg
    assert "JWT" not in msg
    assert "webhook" in msg.lower()


# ---------------------------------------------------------------------------
# FREE predicate (Edge F4) — partitions FREE from ACTIVE / LAPSED on real-shaped
# bodies. Strings mirror account_plan.html's rendered cues (empirically verified).
# ---------------------------------------------------------------------------

# Representative fragments of each real /account/plan body (the load-bearing cue
# words from account_plan.html).
_FREE_BODY = "Free Licence active ✓ Upgrade to Indie 100 reviews/month Upgrade →"
_ACTIVE_PRO_BODY = "Pro Licence active ✓ Renews on 2099-12-31 Last verified"
_LAPSED_BODY = (
    "Pro Subscription ended Your Pro plan ended. Re-subscribe to Pro → "
    "Downgrade to Free"
)


def _free_pred(body: str) -> bool:
    return prov._PLAN_CONVERGED[prov.STATE_FREE](body, body.lower())


def test_free_predicate_matches_free_body():
    assert _free_pred(_FREE_BODY) is True


def test_free_predicate_rejects_active_body():
    # ACTIVE has no "upgrade to" CTA → must not satisfy FREE.
    assert _free_pred(_ACTIVE_PRO_BODY) is False


def test_free_predicate_rejects_lapsed_body():
    # The LAPSED page's "Downgrade to Free" does NOT contain "upgrade to", and the
    # explicit "subscription ended" exclusion is belt-and-suspenders.
    assert _free_pred(_LAPSED_BODY) is False
    # Sanity: the substring the F4 finding feared does NOT occur.
    assert "upgrade" not in "downgrade to free"

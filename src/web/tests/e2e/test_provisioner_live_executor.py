"""HARD-GATE integration test: drive the REAL staging-E2E provisioner HTTP path
against the LOCAL out-of-process app (the ``base_url`` fixture's uvicorn on temp
SQLite), exercising every leg EXCEPT the live-Stripe one.

Why this exists (REVUE-409 review): the provisioner's live HTTP executor
(``scripts/provision_staging_e2e.py``) was previously NEVER run against a real
app — only its pure planner + a mocked Stripe double were unit-tested. Three
production-blocking bugs hid in that gap:

  1. CSRF — ``POST /signup`` / ``POST /login`` are double-submit-protected
     (src/web/csrf.py); a POST without the ``csrf_token`` field is 403'd, so
     nothing ever provisioned.
  2. ``_resolve_user_id`` scraped ``data-user-id`` from a page that rendered no
     such attribute, so it always raised and no paid state ever subscribed.
  3. The duplicate-signup idempotency branch keyed off HTTP 400/409, but the app
     returns HTTP 200 + an "already exists" body.

This test calls the ACTUAL provisioner functions (``_signup_or_login`` →
``_resolve_user_id`` → ``_read_licence_key``) against the live local server, so a
regression in ANY of those legs fails here loudly:
  * CSRF token not submitted → 403 → no session → key read raises.
  * ``data-user-id`` marker missing → ``_resolve_user_id`` raises.
  * key not found on /onboarding → ``_read_licence_key`` raises.

REVUE-409 (signed-synthetic-webhook rework): the paid-state leg is now exercised
locally with the live Stripe API GONE. The tests below sign synthetic
``customer.subscription.*`` events with the test ``STRIPE_WEBHOOK_SECRET`` (set in
conftest.pytest_configure and inherited by the uvicorn child), POST them to the
local ``/webhooks/stripe``, and assert the REAL billing RESULT (the tier upgrade /
lapse) — proving the signature + linkage + DB change end-to-end with ZERO Stripe.

ONE leg stays staging-only BY DESIGN: stamping ``last_validated_at`` needs a JWT
signed by the private key matching the embedded public key in ``jwt_verify.py`` (a
Fly secret absent locally/CI — making that verify key configurable would be a
licence-forgery bypass and is forbidden). So for the ``/account/plan``
"active"/"free" CUES (which additionally require validated), this test stamps
``last_validated_at`` directly in the shared SQLite (exactly how
``seed_active_licence`` fakes it locally — no security seam), then polls. LAPSED
needs no validation, so its page converges from the webhook alone. The
activate→validate JWT leg's real coverage is the in-process unit tests
(``test_licence_activate.py`` / ``test_jwt_verify.py``) + the live staging E2E run.

This is an httpx-only test — it does NOT use Playwright (no ``page`` fixture), so
it runs in the e2e suite via the shared out-of-process server but without a
browser.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.e2e

# The provisioner lives under repo ``scripts/``; the e2e conftest already appends
# that dir to sys.path, so this import resolves the same module the pipeline runs.
import provision_staging_e2e as prov  # noqa: E402
import staging_e2e_accounts as accts  # noqa: E402


def _require_local() -> None:
    """This test asserts the LIVE executor against the LOCAL app. If E2E_BASE_URL
    is set the ``base_url`` fixture targets a deployed env we must not sign up
    throwaway accounts on, so skip — the staging run exercises the real path."""
    if os.environ.get("E2E_BASE_URL"):
        pytest.skip("local-only live-executor test (E2E_BASE_URL is set)")


def test_provisioner_signup_csrf_login_resolves_user_id_and_key(base_url):
    """Fresh account: the real ``_signup_or_login`` must CSRF-sign up, establish a
    session, and read back the licence key; ``_resolve_user_id`` must read the
    ``data-user-id`` marker off /onboarding. Any of the three blockers regressing
    fails this test."""
    _require_local()
    email = f"e2e-live-{uuid.uuid4().hex[:8]}@revue-e2e.test"
    password = "testpass123"  # local app accepts any >=8 char password

    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as client:
        # BLOCKER 1 + 3: CSRF-aware signup that establishes a session and reads
        # the key (a 403 here, or a wrong idempotency branch, makes this raise).
        key = prov._signup_or_login(client, email, password, log=lambda *_: None)
        assert key.startswith("lic_"), f"expected a lic_ key, got {key!r}"

        # BLOCKER 2: the user_id must be readable from the /onboarding marker.
        user_id = prov._resolve_user_id(client, log=lambda *_: None)
        assert user_id.isdigit() and int(user_id) > 0

        # The key the provisioner read must equal what /onboarding renders (the
        # same contract the staging suite relies on).
        onboarding = client.get("/onboarding").text
        assert accts.extract_licence_key(onboarding) == key

    # Exercise the EXACT staging-runtime CSRF-LOGIN + key-read path against the
    # local app. resolve_account_key builds its OWN fresh httpx client (no shared
    # session), so a successful CSRF login is the ONLY way it can reach the
    # authenticated /onboarding key. This closes the gap where signup (which
    # establishes the session via its redirect) would otherwise leave the
    # login leg unproven against a real app — if CSRF-login regresses (403), this
    # raises the clear resolve_account_key RuntimeError.
    runtime_key = accts.resolve_account_key(base_url, email, password)
    assert runtime_key == key


def test_provisioner_login_without_csrf_is_blocked_by_the_app(base_url):
    """Proves the guard the CSRF fix defends against is real on THIS app: a raw
    form POST to /login WITHOUT a csrf_token is rejected with HTTP 403 — so the
    provisioner's GET-then-POST token submission is load-bearing, not cosmetic."""
    _require_local()
    email = f"e2e-live-{uuid.uuid4().hex[:8]}@revue-e2e.test"
    password = "testpass123"

    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=False) as client:
        # Create the account first (via the real CSRF-aware path) so login could
        # otherwise succeed — isolating CSRF as the sole reason for the 403.
        prov._signup_or_login(client, email, password, log=lambda *_: None)
        # Drop the session + CSRF cookies so the bare POST is unauthenticated and
        # token-less, exactly like a cross-site forgery.
        client.cookies.clear()
        resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 403, (
        "expected the CSRF guard to 403 a token-less form POST; got "
        f"{resp.status_code} — the double-submit protection may have regressed"
    )


def test_provisioner_resolve_user_id_raises_without_session(base_url):
    """``_resolve_user_id`` must raise a clear error (not silently return) when
    there is no authenticated session — /onboarding redirects to /login and
    carries no marker."""
    _require_local()
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as client:
        with pytest.raises(RuntimeError) as exc:
            prov._resolve_user_id(client, log=lambda *_: None)
    assert "data-user-id" in str(exc.value)


# ---------------------------------------------------------------------------
# Full signed-synthetic-webhook path — drive the provisioner against the LOCAL app
#
# IMPORTANT — the activate→validate leg is STAGING-ONLY by design. Stamping
# ``last_validated_at`` requires a JWT signed by the private key matching the
# embedded public key in ``jwt_verify.py`` — a Fly secret absent locally/in CI by
# design (making the verify key configurable would be a licence-forgery bypass and
# is forbidden). So on the LOCAL app:
#   * the SIGNED-WEBHOOK code (the new code under test) is fully exercised: we emit
#     each event and assert billing's RESULT (e.g. ``upgraded:...tier=pro``),
#     proving signature + linkage + tier change end-to-end with zero Stripe;
#   * the ``/account/plan`` "active"/"free" cues additionally need
#     ``last_validated_at``, which we stamp DIRECTLY in the shared SQLite the
#     server reads (exactly how ``seed_active_licence`` fakes validation locally —
#     no security seam), then poll the page;
#   * LAPSED needs no validation (is_active=False → lapsed regardless), so its page
#     converges from the webhook alone.
# The activate→validate JWT leg's real coverage lives in the in-process unit tests
# (test_licence_activate.py / test_jwt_verify.py, generated keypair) + the live
# staging E2E run.
# ---------------------------------------------------------------------------

def _local_config(base_url: str, *, password: str) -> "prov.Config":
    """Build a provisioner Config pointing at the LOCAL app, using the same
    STRIPE_WEBHOOK_SECRET + price ids the conftest set in the SERVER's env (so the
    signature verifies and tier_from_price_id resolves)."""
    return prov.Config(
        base_url=base_url.rstrip("/"),
        email_domain="revue-e2e.test",
        webhook_secret=os.environ["STRIPE_WEBHOOK_SECRET"],
        price_indie=os.environ["STRIPE_PRICE_INDIE_MONTHLY"],
        price_pro=os.environ["STRIPE_PRICE_PRO_MONTHLY"],
        account_password=password,
    )


def _signup_and_user_id(base_url: str, email: str, password: str) -> str:
    """Signup (CSRF) + read the user_id off /onboarding, via the REAL provisioner
    functions. Returns the user_id string."""
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as client:
        prov._signup_or_login(client, email, password, log=lambda *_: None)
        return prov._resolve_user_id(client, log=lambda *_: None)


def _stamp_validated(db_path: str, email: str) -> None:
    """Stamp last_validated_at on the account's licence directly in the shared
    SQLite — the SAME mechanism seed_active_licence uses locally to fake the
    staging-only JWT validation leg (no security seam, no real key)."""
    import sqlite3

    from models import (
        get_any_license_for_user,
        get_user_by_email,
        touch_license_validated,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        user = get_user_by_email(conn, email)
        assert user is not None, f"account {email!r} not found in the local DB"
        lic = get_any_license_for_user(conn, user.id)
        assert lic is not None, f"no licence for {email!r}"
        touch_license_validated(conn, lic.id)
        conn.commit()
    finally:
        conn.close()


_ACTIVE_RESULT_TIER = {
    accts.STATE_ACTIVE_PRO: "pro",
    accts.STATE_ACTIVE_PRO_RENEWAL: "pro",
    accts.STATE_ACTIVE_INDIE: "indie",
}


@pytest.mark.parametrize(
    "state", [accts.STATE_ACTIVE_PRO, accts.STATE_ACTIVE_PRO_RENEWAL,
              accts.STATE_ACTIVE_INDIE]
)
def test_signed_webhook_upgrades_active_state_then_page_converges(base_url, _e2e_db, state):
    """The signed-webhook code under test, end-to-end locally with ZERO Stripe:
    sign the synthetic created event → POST to the local /webhooks/stripe → the app
    verifies the HMAC and runs the REAL billing logic → assert the webhook RESULT
    reports the correct tier upgrade. Then (since the active CUE also needs the
    staging-only validated stamp) stamp last_validated_at in the shared DB and poll
    /account/plan to confirm the page renders the Active state with the right tier
    (and renewal date, for the RENEWAL variant)."""
    _require_local()
    cfg = _local_config(base_url, password="testpass123")
    email = f"e2e-{state.lower()}-{uuid.uuid4().hex[:8]}@revue-e2e.test"
    user_id = _signup_and_user_id(base_url, email, "testpass123")

    events = accts.build_subscription_events(
        state, user_id=user_id, price_pro=cfg.price_pro, price_indie=cfg.price_indie
    )
    assert events, f"{state} must emit at least one event"
    for event in events:
        body = accts.emit_subscription_event(cfg.base_url, cfg.webhook_secret, event)
        # The signed event drove the REAL billing tier change (not a skipped no-op).
        assert body["result"].startswith("upgraded"), body
        assert f"tier={_ACTIVE_RESULT_TIER[state]}" in body["result"], body

    # The active /account/plan cue also needs last_validated_at (staging-only JWT
    # leg) — stamp it locally the same way seed_active_licence does, then poll.
    _stamp_validated(_e2e_db, email)
    prov._verify_state(_make_logged_in_client(base_url, email, "testpass123"),
                       state, log=lambda *_: None, timeout=10.0, interval=0.5)


def _make_logged_in_client(base_url: str, email: str, password: str):
    """A CSRF-logged-in httpx client for the verify-poll (kept open by the caller's
    test scope; httpx clients are fine to leave to GC here for a short poll)."""
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0,
                          follow_redirects=True)
    accts.csrf_form_post(client, "/login", "/login",
                         {"email": email, "password": password})
    return client


def test_signed_webhook_free_state_converges_after_validated_stamp(base_url, _e2e_db):
    """FREE emits NO webhook — signup + the (staging-only) validated stamp alone
    must render the free state (Upgrade CTA, no activation command-box). Stamp
    validated locally and poll."""
    _require_local()
    email = f"e2e-free-{uuid.uuid4().hex[:8]}@revue-e2e.test"
    _signup_and_user_id(base_url, email, "testpass123")  # signup + session
    _stamp_validated(_e2e_db, email)
    prov._verify_state(_make_logged_in_client(base_url, email, "testpass123"),
                       accts.STATE_FREE, log=lambda *_: None, timeout=10.0,
                       interval=0.5)


def test_signed_webhook_lapsed_converges_without_validation(base_url):
    """LAPSED needs NO validation (is_active=False → lapsed regardless of
    last_validated_at). The full _execute_state path — signup, CSRF login, user_id,
    the 2-event active→past_due SIGNED sequence, and the verify-poll — runs green
    locally with ZERO Stripe and ZERO DB stamping. This is the part of the new
    webhook machinery that is end-to-end local-provable, and it proves the stable
    synthetic customer links event 1 and is found by event 2 (the lapse)."""
    _require_local()
    cfg = _local_config(base_url, password="testpass123")
    email = f"e2e-lapsed-{uuid.uuid4().hex[:8]}@revue-e2e.test"
    plan = prov.build_state_plan(accts.STATE_LAPSED, cfg)
    plan = prov.StatePlan(state=plan.state, email=email, actions=plan.actions)
    # _execute_state runs signup→activate(no-op locally: no JWT key)→emit→verify.
    prov._execute_state(plan, cfg, log=lambda *_: None)

    # Re-login and assert the lapsed cues explicitly: PRO tier retained, no 'invalid'.
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as client:
        accts.csrf_form_post(client, "/login", "/login",
                             {"email": email, "password": "testpass123"})
        body = client.get("/account/plan").text
    assert "invalid" not in body.lower(), "lapsed copy must never say 'invalid'"
    assert "Re-subscribe" in body, "lapsed account must show the Re-subscribe CTA"


def test_activate_payload_accepted_not_422(base_url):
    """Regression guard for the activate-payload fix: the provisioner posts
    {key, machine_fingerprint} (NOT licence_key). The wrong field 422'd and left
    accounts never-validated. Locally the JWT signing key is unset so activate
    500s on signing — but it must NOT 422 (the payload SHAPE is accepted). On
    staging, where the key is set, the same payload yields a JWT and validates."""
    _require_local()
    email = f"e2e-act-{uuid.uuid4().hex[:8]}@revue-e2e.test"
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as client:
        key = prov._signup_or_login(client, email, "testpass123", log=lambda *_: None)
        resp = client.post(
            "/api/v2/licence/activate",
            json={"key": key, "machine_fingerprint": prov._E2E_FINGERPRINT},
        )
    assert resp.status_code != 422, (
        "activate rejected the provisioner payload as malformed (422) — the "
        f"key/machine_fingerprint shape regressed. Body: {resp.text[:200]}"
    )

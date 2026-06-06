"""Single source of truth for the REVUE-409 staging-E2E account model.

This module owns the things that were previously DUPLICATED across
``scripts/provision_staging_e2e.py`` and ``src/web/tests/e2e/conftest.py``:

  * the canonical licence STATE constants,
  * the deterministic per-state e-mail derivation (``email_for``),
  * the RUNTIME licence-key read (``resolve_account_key``) that REPLACES the old
    ``STAGING_E2E_<STATE>_LICENCE_KEY`` stored secret — the key is read back from
    the live account each run, never pasted into Bitbucket.

Dependency discipline (load-bearing): this module is **stdlib-only at import
time**. ``httpx`` is imported LAZILY inside the functions that need it, and the
module never imports the ``src/web`` package. That keeps both callers
import-clean — the provisioner stays runnable from a lean CI venv, and the e2e
conftest can put ``scripts/`` on ``sys.path`` and import the STATE constants
without dragging in any web/test dependency.

The account model after the REVUE-409 rework:

  * Provisioning is idempotent **ensure-exists** (signup-or-login + activate +
    SIGNED SYNTHETIC WEBHOOKS for the paid states), driven by
    ``scripts/provision_staging_e2e.py`` as a dedicated pipeline step BEFORE the
    E2E step.
  * Accounts share ONE password (``STAGING_E2E_PASSWORD``) and a derived e-mail
    (``e2e-<state>@<domain>``). There are NO per-state secrets.
  * Licence keys are read at RUNTIME via ``resolve_account_key`` — never stored.

Paid-state provisioning — signed synthetic webhooks (no live Stripe)
--------------------------------------------------------------------
The paid states are driven NOT by calling the Stripe API, but by POSTing
HMAC-signed synthetic ``customer.subscription.*`` events directly to the staging
app's ``/webhooks/stripe`` — the exact same endpoint a real Stripe delivery hits.
The app verifies the ``Stripe-Signature`` HMAC against ``STRIPE_WEBHOOK_SECRET``
(``stripe.Webhook.construct_event``) and then runs the real
``process_webhook_event`` linkage/tier logic (src/web/billing.py). So the upgrade
path under test is identical to production, but the provisioner creates ZERO
Stripe objects: no customers, no subscriptions, no PaymentMethods, no API key.

  * ``sign_webhook`` reproduces Stripe's ``t=<ts>,v1=<hex>`` scheme.
  * ``emit_subscription_event`` json-dumps the event ONCE, signs those exact
    bytes, and POSTs them (byte-exact — never re-serialised) with the signature
    header. ``/webhooks/stripe`` is CSRF-exempt, so no csrf token is needed.
  * ``build_subscription_event`` builds the per-state event ``data.object`` with
    ``customer`` (a STABLE synthetic id per account so a 2-event LAPSED sequence
    links), ``status``, ``metadata.user_id`` (the real signed-up uid), the price
    id, and ``current_period_end`` when applicable.

Wipe recovery is trivial: re-run the pipeline. Nothing exists in Stripe to clean
up (we create no Stripe objects); a fresh signup yields a new user_id and the
stable synthetic customer id re-links on the next run.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

# ---------------------------------------------------------------------------
# Canonical licence STATE constants (single source — both callers import these)
# ---------------------------------------------------------------------------

STATE_ACTIVE_PRO = "ACTIVE_PRO"
# Active Pro WITH a non-null current_period_end (renewal date). Split out from
# ACTIVE_PRO so the renewal-date-rendering test (test_active_pro_with_renewal_date)
# runs on staging too: a signed synthetic webhook can carry a fixed
# current_period_end, which a live Stripe subscription never could — closing the
# old AC7 skip. Plain ACTIVE_PRO carries a NULL period_end (the migration-reality
# variant the live path could never produce either).
STATE_ACTIVE_PRO_RENEWAL = "ACTIVE_PRO_RENEWAL"
STATE_ACTIVE_INDIE = "ACTIVE_INDIE"
STATE_FREE = "FREE"
STATE_LAPSED = "LAPSED"
STATE_NOT_ACTIVATED = "NOT_ACTIVATED"

REQUIRED_STATES = [
    STATE_ACTIVE_PRO,
    STATE_ACTIVE_PRO_RENEWAL,
    STATE_ACTIVE_INDIE,
    STATE_FREE,
    STATE_LAPSED,
]
OPTIONAL_STATES = [STATE_NOT_ACTIVATED]
ALL_STATES = REQUIRED_STATES + OPTIONAL_STATES

DEFAULT_EMAIL_DOMAIN = "revue-e2e.test"

# Licence keys render as ``lic_`` + 32 lowercase-hex chars (generate_license_key).
# Assumption (speculative-Low): each authenticated page renders exactly ONE
# licence key, so the FIRST ``lic_`` match is the account's own key. The E2E
# accounts each own a single licence, so there is no second key on the page to
# disambiguate against.
_LICENCE_KEY_RE = re.compile(r"lic_[a-f0-9]{32}")

# The hidden CSRF field every protected HTML form echoes
# (``<input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">``
# — see src/web/csrf.py + signup.html/login.html). We extract its rendered value
# from a GET so the matching form POST passes the double-submit check.
_CSRF_FIELD_RE = re.compile(
    r'name=["\']csrf_token["\'][^>]*\bvalue=["\']([^"\']+)["\']'
)


def extract_csrf_token(html: str) -> "str | None":
    """Extract the rendered ``csrf_token`` hidden-field value from a page.

    The app uses a DOUBLE-SUBMIT cookie (src/web/csrf.py): the middleware sets a
    ``revue_csrf`` cookie (``__Host-revue_csrf`` over HTTPS) AND renders the SAME
    token into ``{{ request.state.csrf_token }}``; ``tokens_match`` accepts the
    POST only when the submitted ``csrf_token`` form field equals the cookie value
    (constant-time) and is validly signed.

    We read the token from the rendered HIDDEN FIELD rather than from the cookie
    jar on purpose: it is what a browser submits, it is guaranteed equal to the
    cookie (both come from ``request.state.csrf_token``), and — critically — it is
    immune to the cookie-NAME divergence between environments (the cookie is
    ``revue_csrf`` locally but ``__Host-revue_csrf`` on staging HTTPS, so reading
    the jar by a literal name would silently break on staging). Returns the token
    string, or ``None`` if the page renders no such field. Pure.
    """
    m = _CSRF_FIELD_RE.search(html or "")
    return m.group(1) if m else None


def csrf_form_post(client, get_path: str, post_path: str, data: dict):
    """GET ``get_path`` to mint the CSRF cookie + read its token, then POST
    ``post_path`` echoing that token as the ``csrf_token`` field.

    The app's CSRF middleware (src/web/csrf.py) blocks every form-encoded,
    non-exempt POST (``/signup`` and ``/login`` are NOT exempt) with HTTP 403
    unless the body carries a ``csrf_token`` equal to the ``revue_csrf`` cookie.
    httpx's ``Client`` cookie jar auto-sends the cookie the GET set; we only have
    to supply the matching form field. Mutates ``data`` is avoided — a copy is
    submitted. Returns the POST response.

    If the GET page renders no CSRF field (e.g. an already-authenticated redirect)
    the POST is still attempted WITHOUT a token — the caller handles the outcome.
    """
    page = client.get(get_path)
    token = extract_csrf_token(page.text)
    body = dict(data)
    if token:
        body["csrf_token"] = token
    return client.post(post_path, data=body)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O) — unit tested
# ---------------------------------------------------------------------------

def email_for(state: str, domain: str = DEFAULT_EMAIL_DOMAIN) -> str:
    """Stable, E2E-owned e-mail for a STATE: ``e2e-<state-lowercase>@<domain>``.

    Pure and idempotent across re-runs — the provisioner relies on the same
    e-mail resolving to the same account so ensure-exists never duplicates.
    """
    return f"e2e-{state.lower()}@{domain}"


def extract_licence_key(html: str) -> "str | None":
    """Extract the first ``lic_<32 hex>`` licence key from a rendered page.

    Returns the key string, or ``None`` if the page carries no key. Pure.
    """
    m = _LICENCE_KEY_RE.search(html or "")
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Runtime key read (httpx — lazily imported) — REPLACES the stored _LICENCE_KEY
# ---------------------------------------------------------------------------

def _httpx():
    """Lazy ``httpx`` accessor so the module stays stdlib-only at import time.

    Indirected through a function so unit tests can monkeypatch it without a
    real httpx install or any network.
    """
    import httpx

    return httpx


def resolve_account_key(base_url: str, email: str, password: str) -> str:
    """Log in as ``email`` and read the account's licence key from a live page.

    This is the RUNTIME replacement for the removed
    ``STAGING_E2E_<STATE>_LICENCE_KEY`` secret: the key is read back from the
    account each run rather than pasted into Bitbucket. Side-effect-free beyond
    the login that is required to reach an authenticated page.

    Flow: ``POST /login`` (follow redirects) → ``GET /onboarding`` (fallback
    ``GET /dashboard``, then ``GET /account/plan``) → extract ``lic_<32 hex>``.
    /account/plan uses ``get_any_license_for_user`` (unfiltered), so the key is
    visible even for lapsed accounts (``is_active=False``). /onboarding and
    /dashboard use the filtered ``get_license_for_user`` which hides lapsed rows,
    so without the /account/plan fallback the lapsed key is never returned.
    Raises a clear, actionable ``RuntimeError`` NAMING the account if no key
    can be read.
    """
    httpx = _httpx()
    base = base_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0, follow_redirects=True) as client:
        # CSRF-aware login: GET /login mints the revue_csrf cookie + renders the
        # token; the POST echoes it as csrf_token, else the double-submit guard
        # 403s the form POST (src/web/csrf.py) and login silently never happens.
        csrf_form_post(
            client, "/login", "/login", {"email": email, "password": password}
        )
        for path in ("/onboarding", "/dashboard", "/account/plan"):
            key = extract_licence_key(client.get(path).text)
            if key:
                return key
    raise RuntimeError(
        f"Could not read the licence key for staging E2E account {email!r} from "
        f"/onboarding, /dashboard, or /account/plan after login. The account may "
        f"not exist or may not be activated — re-run the provision-staging-e2e "
        f"pipeline step (ensure-exists). See docs/runbooks/staging-e2e-account.md."
    )


# ---------------------------------------------------------------------------
# Signed synthetic Stripe webhooks (stdlib-only) — REPLACES the live-Stripe leg
# ---------------------------------------------------------------------------
#
# We drive the paid states by POSTing HMAC-signed synthetic subscription events
# to the staging app's /webhooks/stripe — the same endpoint a real Stripe
# delivery hits. The app verifies the signature against STRIPE_WEBHOOK_SECRET via
# stripe.Webhook.construct_event, then runs the REAL process_webhook_event logic.
# No Stripe API, no key, no Stripe objects created.

# A fixed far-future renewal date for the ACTIVE_PRO_RENEWAL account: the page
# asserts this exact ISO date. A live Stripe sub could never carry a forced
# literal — a synthetic event can. 2099-12-31T00:00:00Z in epoch seconds.
RENEWAL_EPOCH_2099 = 4102358400
# A nearer-future renewal for the Indie + LAPSED-base events (any future epoch).
RENEWAL_EPOCH_FUTURE = 4070908800  # 2099-01-01T00:00:00Z


def sign_webhook(payload: str, secret: str, timestamp: int) -> str:
    """Return a Stripe-style ``Stripe-Signature`` header value for ``payload``.

    Reproduces Stripe's signing scheme (the one ``stripe.Webhook.construct_event``
    verifies): the signed message is ``f"{timestamp}.{payload}"`` and the v1
    signature is its HMAC-SHA256 under ``secret`` (the ``whsec_...`` string used
    verbatim as the key — Stripe does NOT base64-decode it). The header is
    ``t=<timestamp>,v1=<hex>``.

    ``payload`` MUST be the exact string that will be sent as the request body —
    the verifier recomputes the HMAC over the raw received bytes, so signing a
    re-serialised copy (different whitespace/key-order) fails. Pure.
    """
    signed = f"{timestamp}.{payload}".encode("utf-8")
    v1 = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={v1}"


def synthetic_customer_id(state: str) -> str:
    """A STABLE synthetic Stripe customer id for a state's account.

    Stable across re-runs (derived only from the state) so a 2-event LAPSED
    sequence links: event 1 (no customer yet) falls back to ``metadata.user_id``
    and records this id; event 2 is then found by it via
    ``get_user_by_stripe_customer``. ``cus_`` prefix mirrors Stripe's shape.
    Wipe-safe: a fresh signup gives a new user_id, and event 1 re-links this same
    id to it — no Stripe object exists to orphan. Pure.
    """
    return f"cus_e2e_{state.lower()}"


def _subscription_object(*, customer: str, user_id: str, status: str,
                         price_id: str, period_end: "int | None") -> dict:
    """Build one webhook ``data.object`` (a subscription) shaped exactly as
    billing.process_webhook_event reads it (billing.py 355-461):
    ``customer`` / ``status`` / ``metadata.user_id`` / ``items.data[0].price.id``
    / optional ``current_period_end``. Pure."""
    obj: dict = {
        "customer": customer,
        "status": status,
        "metadata": {"user_id": str(user_id)},
        "items": {"data": [{"price": {"id": price_id}}]},
    }
    if period_end is not None:
        obj["current_period_end"] = period_end
    return obj


def _event(event_type: str, obj: dict) -> dict:
    """Wrap a subscription object in a Stripe event envelope. Pure.

    The top-level ``"object": "event"`` + ``id`` are LOAD-BEARING, not cosmetic:
    ``stripe.Webhook.construct_event`` (which the staging app calls) reads
    ``event.object`` to distinguish v1/v2 events AFTER verifying the signature.
    Omitting it makes construct_event raise (the app catches that and returns
    HTTP 400), so a signed-but-envelope-less event would be rejected even though
    the signature is valid. The ``id`` is a synthetic, stable-ish event id.
    """
    digest = hashlib.sha256(event_type.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"evt_e2e_{digest}",
        "object": "event",
        "type": event_type,
        "data": {"object": obj},
    }


# Per-state event SPECS as a registry (not an if/elif ladder — OCP, mirrors the
# project's no-platform-elif rule). Each entry maps a STATE to a function that,
# given the resolved (user_id, price ids), returns the ORDERED list of synthetic
# events to POST. States absent from the registry emit NO event (FREE,
# NOT_ACTIVATED — the activate round-trip / signup alone suffices).
def _events_active_pro_null(uid, pro, indie):
    # ACTIVE_PRO: omit current_period_end → billing writes NULL (the
    # migration-reality variant live Stripe could never produce).
    return [_event("customer.subscription.created", _subscription_object(
        customer=synthetic_customer_id(STATE_ACTIVE_PRO), user_id=uid,
        status="active", price_id=pro, period_end=None))]


def _events_active_pro_renewal(uid, pro, indie):
    # ACTIVE_PRO_RENEWAL: carry the fixed 2099 renewal date the page asserts.
    return [_event("customer.subscription.created", _subscription_object(
        customer=synthetic_customer_id(STATE_ACTIVE_PRO_RENEWAL), user_id=uid,
        status="active", price_id=pro, period_end=RENEWAL_EPOCH_2099))]


def _events_active_indie(uid, pro, indie):
    return [_event("customer.subscription.created", _subscription_object(
        customer=synthetic_customer_id(STATE_ACTIVE_INDIE), user_id=uid,
        status="active", price_id=indie, period_end=RENEWAL_EPOCH_FUTURE))]


def _events_lapsed(uid, pro, indie):
    # LAPSED is a 2-event sequence on a STABLE customer id: first go active+PRO
    # (so the tier is set + the customer linked), THEN past_due (billing's lapse
    # branch flips is_active=False and RETAINS the tier). Order matters and is
    # guaranteed by sequential synchronous POSTs.
    cust = synthetic_customer_id(STATE_LAPSED)
    return [
        _event("customer.subscription.created", _subscription_object(
            customer=cust, user_id=uid, status="active", price_id=pro,
            period_end=RENEWAL_EPOCH_FUTURE)),
        _event("customer.subscription.updated", _subscription_object(
            customer=cust, user_id=uid, status="past_due", price_id=pro,
            period_end=RENEWAL_EPOCH_FUTURE)),
    ]


_STATE_EVENT_BUILDERS = {
    STATE_ACTIVE_PRO: _events_active_pro_null,
    STATE_ACTIVE_PRO_RENEWAL: _events_active_pro_renewal,
    STATE_ACTIVE_INDIE: _events_active_indie,
    STATE_LAPSED: _events_lapsed,
}


def build_subscription_events(state: str, *, user_id: str, price_pro: str,
                              price_indie: str) -> "list[dict]":
    """Return the ORDERED synthetic webhook events for ``state`` (possibly empty).

    Pure: builds the event dicts only; ``emit_subscription_event`` does the I/O.
    States with no paid subscription (FREE, NOT_ACTIVATED) return ``[]``.
    """
    builder = _STATE_EVENT_BUILDERS.get(state)
    if builder is None:
        return []
    return builder(user_id, price_pro, price_indie)


# Map a subscription STATUS to the prefix billing.process_webhook_event returns
# for that state (verified against src/web/billing.py): active/trialing →
# "upgraded:", past_due/unpaid → "lapsed:", canceled → "downgraded:". Mirrors the
# server's own _SUBSCRIPTION_STATUS_STATE so a billing regression (e.g. a past_due
# event that erroneously upgrades) is caught at the POST, not 60s later at the
# verify-poll. A status with no definite billing effect (e.g. incomplete →
# no_change) yields None — emit then only checks the non-skipped contract.
_STATUS_RESULT_PREFIX = {
    "active": "upgraded",
    "trialing": "upgraded",
    "past_due": "lapsed",
    "unpaid": "lapsed",
    "canceled": "downgraded",
}


def expected_result_prefix(event: dict) -> "str | None":
    """Return the billing result-prefix expected for ``event`` (or None).

    Pure. Derived from the event's OWN ``data.object.status`` (NOT the account
    state) so the two LAPSED events — created/active and updated/past_due — each
    get their correct, DIFFERENT expectation (``upgraded`` then ``lapsed``). This
    is exactly the regression class the per-event check must surface: a past_due
    event that comes back ``upgraded`` is a billing bug, caught at emit time.
    """
    status = ((event.get("data") or {}).get("object") or {}).get("status")
    return _STATUS_RESULT_PREFIX.get(status)


_UNSET = object()


def emit_subscription_event(base_url: str, secret: str, event: dict,
                            *, client=None, expected_prefix=_UNSET) -> dict:
    """Sign and POST ONE synthetic event to ``/webhooks/stripe``; assert success.

    json-dumps the event ONCE and signs those exact bytes (byte-exact — the body
    sent is the signed string, never a re-serialised copy, or the server's HMAC
    over the raw bytes would not match). Sends ``Content-Type: application/json``
    + ``Stripe-Signature``; ``/webhooks/stripe`` is CSRF-exempt so no csrf token.
    Uses a near-now timestamp (construct_event enforces a 300s tolerance).

    Result assertion (Edge F1): always rejects a ``skipped:`` no-op (billing
    returns 200 with ``skipped:unknown_price`` / ``skipped:unknown_customer`` on a
    no-op, a false green for a status-only check). ADDITIONALLY, unless
    ``expected_prefix`` is passed, the expected prefix is derived from the event's
    OWN status via ``expected_result_prefix`` and the result must START WITH it —
    so a billing regression (e.g. a past_due event that comes back ``upgraded``)
    fails HERE at the POST, not 60s later at the verify-poll. Pass
    ``expected_prefix=None`` to opt out of the prefix check (skipped-only), or an
    explicit string to override the derived one. Returns the parsed JSON body. An
    optional ``client`` (an httpx.Client) is reused when given (the provisioner
    shares one); otherwise a transient client is created.
    """
    payload = json.dumps(event)
    sig = sign_webhook(payload, secret, int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "Stripe-Signature": sig,
    }

    def _post(c):
        return c.post("/webhooks/stripe", content=payload, headers=headers)

    if client is not None:
        resp = _post(client)
    else:
        httpx = _httpx()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0) as c:
            resp = _post(c)

    assert resp.status_code == 200, (
        f"/webhooks/stripe rejected the synthetic event (HTTP "
        f"{resp.status_code}): {getattr(resp, 'text', '')[:300]} — check "
        f"STRIPE_WEBHOOK_SECRET matches the app and the payload bytes are signed "
        f"verbatim."
    )
    body = resp.json()
    result = str(body.get("result", ""))
    assert not result.startswith("skipped"), (
        f"webhook processed but was a NO-OP (result={result!r}) — the event did "
        f"not change state. Likely an unknown price id or an unlinked customer. "
        f"Check STRIPE_PRICE_* match the app and metadata.user_id is correct."
    )
    prefix = expected_result_prefix(event) if expected_prefix is _UNSET else expected_prefix
    if prefix is not None:
        assert result.startswith(prefix), (
            f"webhook result {result!r} does not start with the expected "
            f"{prefix!r} for a {((event.get('data') or {}).get('object') or {}).get('status')!r} "
            f"event — billing took the WRONG action (e.g. a past_due event that "
            f"upgraded instead of lapsing). This is a billing regression, not a "
            f"provisioning config issue."
        )
    return body

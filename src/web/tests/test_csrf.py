"""Tests for REVUE-418 — systemic CSRF protection.

Covers two layers:
  1. The ``csrf`` helper module (token mint / verify, signing, constant-time
     comparison).
  2. The application middleware behaviour: protected form POSTs require a
     valid double-submit token (→ 403 otherwise), while token-authenticated
     API calls and the signature-verified Stripe webhook are exempt by path.

The middleware tests deliberately use a RAW client (httpx against the ASGI
app) rather than the CSRF-aware ``client`` fixture, so they exercise the real
enforcement path without the test harness injecting tokens for them.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from csrf import (
    CSRF_COOKIE_BASE,
    CSRF_FORM_FIELD,
    MAX_BODY_BYTES,
    csrf_cookie_name,
    issue_token,
    tokens_match,
)


# =====================================================================
# csrf.py helper unit tests
# =====================================================================

def test_issue_token_returns_nonempty_string():
    token = issue_token()
    assert isinstance(token, str)
    assert token


def test_issue_token_is_unique_per_call():
    # Each freshly issued token embeds fresh randomness, so two calls differ.
    assert issue_token() != issue_token()


def test_tokens_match_true_for_identical_tokens():
    token = issue_token()
    assert tokens_match(token, token) is True


def test_tokens_match_false_for_different_tokens():
    assert tokens_match(issue_token(), issue_token()) is False


def test_tokens_match_false_for_empty_cookie():
    assert tokens_match("", issue_token()) is False


def test_tokens_match_false_for_empty_form_value():
    assert tokens_match(issue_token(), "") is False


def test_tokens_match_false_for_both_empty():
    assert tokens_match("", "") is False


def test_tokens_match_false_for_tampered_token():
    token = issue_token()
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert tokens_match(token, tampered) is False


# =====================================================================
# Middleware enforcement — raw client (no auto-injection)
# =====================================================================

@pytest_asyncio.fixture
async def raw_client(_tmp_db) -> AsyncClient:
    """A client that does NOT auto-inject CSRF tokens, for testing the real
    enforcement path. Distinct from the conftest ``client`` fixture."""
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_protected_form_post_without_token_is_403(raw_client: AsyncClient):
    """Test Case 1 — POST a protected form without a token → 403 (explicit)."""
    resp = await raw_client.post(
        "/login",
        data={"email": "x@test.com", "password": "password1"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_protected_form_post_with_mismatched_token_is_403(raw_client: AsyncClient):
    """A form token that does not match the cookie → 403."""
    # Seed a CSRF cookie via a GET, then submit a DIFFERENT token in the form.
    await raw_client.get("/login")
    resp = await raw_client.post(
        "/login",
        data={
            "email": "x@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: "not-the-cookie-value",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_protected_form_post_with_valid_token_succeeds(raw_client: AsyncClient):
    """Test Case 2 — POST with a valid token → succeeds (reaches handler)."""
    get_resp = await raw_client.get("/signup")
    token = get_resp.cookies.get(CSRF_COOKIE_BASE)
    assert token, "GET must set the CSRF cookie"

    resp = await raw_client.post(
        "/signup",
        data={
            "email": "csrf-valid@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: token,
        },
        follow_redirects=False,
    )
    # Reaching the handler => the real signup redirect, not a 403.
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"


@pytest.mark.asyncio
async def test_valid_token_checkout_reaches_handler_with_body_intact(
    raw_client: AsyncClient, monkeypatch
):
    """GO/NO-GO body-re-read gate — a valid-token POST to /billing/checkout
    must reach the handler with the form fields INTACT.

    If the middleware consumes the body to read the token and the downstream
    handler then sees an empty form, ``tier`` is blank → the route redirects
    to ``/billing?error=invalid_tier``. So we assert the redirect TARGET is the
    real Stripe checkout URL, NOT merely status 303 — a body-re-read failure
    still returns 303 to the error page and would pass a bare status check.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_test_indie")

    # Authenticate (signup, with token) so checkout passes the session gate.
    su = await raw_client.get("/signup")
    su_token = su.cookies.get(CSRF_COOKIE_BASE)
    await raw_client.post(
        "/signup",
        data={
            "email": "checkout-body@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: su_token,
        },
        follow_redirects=False,
    )

    # Re-read the (stable) CSRF cookie for the checkout POST.
    token = raw_client.cookies.get(CSRF_COOKIE_BASE)
    assert token

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/cs_body_intact"
    with patch("stripe.checkout.Session.create", return_value=mock_session):
        resp = await raw_client.post(
            "/billing/checkout",
            data={
                "tier": "indie",
                "interval": "month",
                CSRF_FORM_FIELD: token,
            },
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://checkout.stripe.com/pay/cs_body_intact", (
        "checkout must reach the handler with tier/interval intact — a redirect "
        "to /billing?error=invalid_tier means the middleware ate the form body"
    )


@pytest.mark.asyncio
async def test_billing_portal_without_token_is_403(raw_client: AsyncClient, monkeypatch):
    """Guard: /billing/portal is a BODYLESS protected form POST (it acts on the
    session cookie, not a request body). It must STILL require a CSRF token —
    a bodyless / empty-content-type POST is cross-site forgeable, so without a
    token it must 403. This pins the content-type rule so a future change that
    skips empty-content-type requests cannot silently unprotect this route.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    # Authenticate first so the 403 is the CSRF gate, not the auth redirect.
    su = await raw_client.get("/signup")
    await raw_client.post(
        "/signup",
        data={
            "email": "portal-noscrf@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: su.cookies.get(CSRF_COOKIE_BASE),
        },
        follow_redirects=False,
    )
    resp = await raw_client.post("/billing/portal", follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_json_post_to_unmatched_path_is_not_csrf_blocked(raw_client: AsyncClient):
    """An ``application/json`` POST forces a CORS preflight an attacker cannot
    satisfy, so it is not a CSRF vector and must NOT be blocked by CSRF. A JSON
    POST to a non-existent path should reach routing and 404 — never a CSRF 403.
    """
    resp = await raw_client.post("/no-such-route", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_csrf_cookie_is_set_on_get_for_unauthenticated_pages(raw_client: AsyncClient):
    """A GET that lacks the CSRF cookie gets one set, so unauthenticated
    login/signup forms carry a usable token."""
    resp = await raw_client.get("/login")
    assert resp.cookies.get(CSRF_COOKIE_BASE), "GET /login must mint a CSRF cookie"


@pytest.mark.asyncio
async def test_csrf_cookie_is_stable_across_gets(raw_client: AsyncClient):
    """The CSRF cookie is read-or-generate, NOT rotated per request — a second
    GET that already carries the cookie must not replace it (multi-tab safety)."""
    first = await raw_client.get("/login")
    token = first.cookies.get(CSRF_COOKIE_BASE)
    assert token
    # cookie now in the jar; a second GET should not issue a new value
    second = await raw_client.get("/signup")
    # If the middleware re-set the cookie, httpx would expose it on the response.
    assert second.cookies.get(CSRF_COOKIE_BASE) in (None, token)
    assert raw_client.cookies.get(CSRF_COOKIE_BASE) == token


# =====================================================================
# Exemptions — must NOT be blocked by CSRF
# =====================================================================

@pytest.mark.asyncio
async def test_stripe_webhook_processed_without_csrf_token(
    raw_client: AsyncClient, monkeypatch, _tmp_db
):
    """Test Case 3 — Stripe webhook POST (valid signature, no CSRF token) is
    still processed. CSRF must skip the signature-verified webhook entirely."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_pro_csrf")

    from database import get_db
    from models import create_user, create_workspace, create_license_key
    with get_db() as conn:
        uid = create_user(conn, "wh-csrf@test.com", "hash")
        wsid = create_workspace(conn, uid, "ws")
        create_license_key(conn, wsid, "lic_wh_csrf")
        conn.execute("UPDATE users SET stripe_customer_id = 'cus_wh_csrf' WHERE id = ?", (uid,))

    payload = json.dumps({
        "type": "customer.subscription.created",
        "data": {"object": {
            "customer": "cus_wh_csrf",
            "items": {"data": [{"price": {"id": "price_pro_csrf"}}]},
        }},
    }).encode()
    mock_event = json.loads(payload)

    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        resp = await raw_client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": "t=1,v1=abc"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_v2_licence_activate_works_without_csrf_token(
    raw_client: AsyncClient, monkeypatch, _tmp_db
):
    """Test Case 4 — a /v2 licence API call without a CSRF token still works.
    The endpoint authenticates via the body key, not the session cookie, so it
    is exempt by path (``/api/...``). We assert it is NOT a 403 (i.e. CSRF did
    not block it); the licence-specific response is covered elsewhere."""
    from database import get_db
    from models import create_user, create_workspace, create_license_key
    with get_db() as conn:
        uid = create_user(conn, "api-csrf@test.com", "hash")
        wsid = create_workspace(conn, uid, "ws")
        create_license_key(conn, wsid, "lic_api_csrf", tier="indie", reviews_limit=None)

    resp = await raw_client.post(
        "/api/v2/licence/activate",
        json={"key": "lic_api_csrf"},
    )
    assert resp.status_code != 403, "JWT/body-authenticated API must be CSRF-exempt"


@pytest.mark.asyncio
async def test_api_v2_validate_works_without_csrf_token(
    raw_client: AsyncClient, monkeypatch, _tmp_db
):
    """A second /api/v2 endpoint (validate) must also be CSRF-exempt by path."""
    resp = await raw_client.post(
        "/api/v2/licence/validate",
        json={"jwt": "not-a-real-jwt"},
    )
    # An invalid JWT yields a normal application response (e.g. {valid: false}),
    # never a CSRF 403.
    assert resp.status_code != 403


# =====================================================================
# Rendered forms carry the hidden token (Test Case 5)
# =====================================================================

@pytest.mark.asyncio
async def test_login_form_contains_csrf_token(raw_client: AsyncClient):
    resp = await raw_client.get("/login")
    token = raw_client.cookies.get(CSRF_COOKIE_BASE)
    assert token
    body = resp.text
    assert f'name="{CSRF_FORM_FIELD}"' in body
    # the rendered value must be the REAL token, not just the field name
    assert token in body


@pytest.mark.asyncio
async def test_signup_form_contains_csrf_token(raw_client: AsyncClient):
    resp = await raw_client.get("/signup")
    token = raw_client.cookies.get(CSRF_COOKIE_BASE)
    assert token
    body = resp.text
    assert f'name="{CSRF_FORM_FIELD}"' in body
    assert token in body


@pytest.mark.asyncio
async def test_billing_forms_contain_csrf_token(raw_client: AsyncClient, monkeypatch):
    # AA GAP1 — pin STRIPE_SECRET_KEY (+ a price var) explicitly so the >=2
    # token-count assertion is real and not silently dependent on the ambient
    # env: the /billing checkout form only renders when Stripe is configured.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_pinned")
    monkeypatch.setenv("STRIPE_PRICE_INDIE_MONTHLY", "price_test_indie")
    # Authenticate to render the billing page (has checkout + portal forms).
    su = await raw_client.get("/signup")
    await raw_client.post(
        "/signup",
        data={
            "email": "billing-form@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: su.cookies.get(CSRF_COOKIE_BASE),
        },
        follow_redirects=False,
    )
    resp = await raw_client.get("/billing")
    token = raw_client.cookies.get(CSRF_COOKIE_BASE)
    body = resp.text
    # Both POST forms on the page must carry the hidden token.
    assert body.count(f'name="{CSRF_FORM_FIELD}"') >= 2
    assert token in body


@pytest.mark.asyncio
async def test_account_plan_resubscribe_form_contains_csrf_token(
    raw_client: AsyncClient, monkeypatch, _tmp_db
):
    """The account_plan re-subscribe form (lapsed state) POSTs to
    /billing/checkout and must carry the hidden token."""
    from database import get_db
    from models import create_user, create_workspace, create_license_key

    su = await raw_client.get("/signup")
    await raw_client.post(
        "/signup",
        data={
            "email": "plan-form@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: su.cookies.get(CSRF_COOKIE_BASE),
        },
        follow_redirects=False,
    )
    # Drive the user's licence into the LAPSED state so the re-subscribe form
    # renders (it only appears in state == "lapsed").
    from models import get_user_by_email, set_license_subscription_state
    with get_db() as conn:
        user = get_user_by_email(conn, "plan-form@test.com")
        conn.execute("UPDATE users SET tier='pro' WHERE id=?", (user.id,))
        # Lapsed state requires the licence row's tier to be a PAID tier
        # (is_active=False AND tier != "free"); the signup row is free.
        conn.execute(
            "UPDATE license_keys SET tier='pro' "
            "WHERE workspace_id IN (SELECT id FROM workspaces WHERE user_id=?)",
            (user.id,),
        )
        set_license_subscription_state(
            conn, user.id,
            is_active=False,
            subscription_status="past_due",
            current_period_end="2025-12-31T00:00:00+00:00",
        )

    resp = await raw_client.get("/account/plan")
    token = raw_client.cookies.get(CSRF_COOKIE_BASE)
    body = resp.text
    assert "Re-subscribe" in body, "lapsed state should render the re-subscribe form"
    assert f'name="{CSRF_FORM_FIELD}"' in body
    assert token in body


# =====================================================================
# Unsafe-method coverage — PUT / PATCH / DELETE (regression guards)
# =====================================================================
# Only POST has a real protected handler. /login is registered for GET+POST,
# so a PUT/PATCH/DELETE to it is enforced by the CSRF middleware (which runs
# before routing). The load-bearing assertion is the CSRF discriminator:
#   no token  -> 403 (CSRF blocked it)
#   token     -> != 403 (CSRF let it through; routing then 405s the verb)

@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
@pytest.mark.asyncio
async def test_unsafe_method_without_token_is_403(raw_client: AsyncClient, method):
    """Every unsafe method (not just POST) is CSRF-enforced: no valid token
    on a form-encoded body → 403."""
    resp = await raw_client.request(
        method,
        "/login",
        data={"email": "x@test.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
@pytest.mark.asyncio
async def test_unsafe_method_with_valid_token_passes_csrf(raw_client: AsyncClient, method):
    """A valid token lets the unsafe method through the CSRF gate. /login has no
    handler for these verbs, so routing returns 405 — the point is it is NOT a
    CSRF 403, proving the gate accepted the token for PUT/PATCH/DELETE too."""
    await raw_client.get("/login")
    token = raw_client.cookies.get(CSRF_COOKIE_BASE)
    assert token
    resp = await raw_client.request(
        method,
        "/login",
        data={"email": "x@test.com", CSRF_FORM_FIELD: token},
        follow_redirects=False,
    )
    assert resp.status_code != 403, "valid token must pass CSRF for all unsafe methods"


# =====================================================================
# multipart/form-data token extraction (regression guard)
# =====================================================================

@pytest.mark.asyncio
async def test_multipart_form_token_is_extracted_and_accepted(raw_client: AsyncClient):
    """A multipart/form-data body is a CORS-simple, parseable form type: the
    middleware must extract the token from it and accept a matching value."""
    get_resp = await raw_client.get("/signup")
    token = get_resp.cookies.get(CSRF_COOKIE_BASE)
    assert token
    # httpx sends multipart/form-data when ``files=`` is present; carry the
    # token as a regular multipart field via ``data=``.
    resp = await raw_client.post(
        "/signup",
        data={
            "email": "multipart-csrf@test.com",
            "password": "password1",
            CSRF_FORM_FIELD: token,
        },
        files={"_ignored": ("x.txt", b"x", "text/plain")},
        follow_redirects=False,
    )
    # Reaching the handler (303 redirect) proves the token was parsed out of the
    # multipart body and matched — not a CSRF 403.
    assert resp.status_code != 403, "multipart token must be extracted and accepted"


@pytest.mark.asyncio
async def test_multipart_form_without_token_is_403(raw_client: AsyncClient):
    """A multipart POST with NO token still fails closed (403)."""
    resp = await raw_client.post(
        "/signup",
        data={"email": "multipart-notok@test.com", "password": "password1"},
        files={"_ignored": ("x.txt", b"x", "text/plain")},
        follow_redirects=False,
    )
    assert resp.status_code == 403


# =====================================================================
# Invalid-cookie healing (fix #1 + #2) — TDD red
# =====================================================================

@pytest.mark.asyncio
async def test_get_with_invalid_cookie_mints_fresh_cookie(raw_client: AsyncClient):
    """A GET carrying a garbage/tampered CSRF cookie (e.g. after SECRET_KEY
    rotation) must be re-minted: the response sets a NEW, validly-signed cookie
    instead of trusting the bad value. Otherwise the browser is locked out."""
    raw_client.cookies.set(CSRF_COOKIE_BASE, "garbage-not-signed")
    resp = await raw_client.get("/login")
    new_token = resp.cookies.get(CSRF_COOKIE_BASE)
    assert new_token, "a GET with an invalid cookie must mint a fresh one"
    assert new_token != "garbage-not-signed"
    # The minted token must itself be valid (echoing it back will match).
    assert tokens_match(new_token, new_token) is True


@pytest.mark.asyncio
async def test_post_403_with_invalid_cookie_sets_fresh_cookie(raw_client: AsyncClient):
    """Fix #2 — a protected POST whose cookie is missing/garbage returns 403 AND
    sets a freshly-minted cookie on that 403, so the browser can recover. Without
    this, a first-visit / cookie-cleared POST 403s forever (no cookie to echo)."""
    raw_client.cookies.set(CSRF_COOKIE_BASE, "garbage-not-signed")
    resp = await raw_client.post(
        "/login",
        data={"email": "heal@test.com", "password": "password1"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    healed = resp.cookies.get(CSRF_COOKIE_BASE)
    assert healed, "the 403 must set a fresh CSRF cookie so the browser recovers"
    assert healed != "garbage-not-signed"
    assert tokens_match(healed, healed) is True


@pytest.mark.asyncio
async def test_invalid_cookie_then_matched_post_succeeds(raw_client: AsyncClient):
    """End-to-end healing: after a garbage cookie is replaced by a fresh one
    (via the 403's Set-Cookie, which httpx stores), echoing the new cookie value
    in the form makes the next POST pass CSRF."""
    raw_client.cookies.set(CSRF_COOKIE_BASE, "garbage-not-signed")
    first = await raw_client.post(
        "/login",
        data={"email": "heal2@test.com", "password": "password1"},
        follow_redirects=False,
    )
    assert first.status_code == 403
    # Read the healed cookie from the RESPONSE (the jar now holds both the stale
    # garbage value and the new one, which would raise CookieConflict on .get).
    healed = first.cookies.get(CSRF_COOKIE_BASE)
    assert healed and healed != "garbage-not-signed"
    # Replace the jar's stale cookie with the healed value, then echo it back;
    # CSRF must now accept it (not 403).
    raw_client.cookies.set(CSRF_COOKIE_BASE, healed)
    resp = await raw_client.post(
        "/login",
        data={"email": "heal2@test.com", "password": "password1", CSRF_FORM_FIELD: healed},
        follow_redirects=False,
    )
    assert resp.status_code != 403, "after healing, a matched-token POST must pass CSRF"


# =====================================================================
# Body-cap defense-in-depth (fix #3) — TDD red
# =====================================================================

@pytest.mark.asyncio
async def test_oversized_unsafe_body_is_rejected_413(raw_client: AsyncClient):
    """An unsafe-method, form-encoded body larger than MAX_BODY_BYTES is rejected
    with 413 BEFORE the middleware buffers/parses it — bounding memory use."""
    oversized = b"x" * (MAX_BODY_BYTES + 1)
    resp = await raw_client.post(
        "/login",
        content=oversized,
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_body_at_cap_is_not_rejected_as_oversized(raw_client: AsyncClient):
    """A body exactly at the cap is NOT rejected for size — it proceeds to the
    normal CSRF check (which 403s here for lack of a valid token, not 413)."""
    at_cap = b"a=" + b"x" * (MAX_BODY_BYTES - 2)  # exactly MAX_BODY_BYTES bytes
    assert len(at_cap) == MAX_BODY_BYTES
    resp = await raw_client.post(
        "/login",
        content=at_cap,
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 403, "at-cap body proceeds to CSRF check, not a 413"


# =====================================================================
# Cookie hardening — __Host- prefix + Secure (REVUE-418 round 2)
# =====================================================================
# The default (insecure) mode is exercised by every middleware test above
# (plain ``revue_csrf`` name, no Secure). These pin the secure-mode behaviour.

def test_csrf_cookie_name_defaults_to_plain(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert csrf_cookie_name() == CSRF_COOKIE_BASE
    assert CSRF_COOKIE_BASE == CSRF_COOKIE_BASE  # alias is the insecure name


def test_csrf_cookie_name_is_host_prefixed_in_secure_mode(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "1")
    assert csrf_cookie_name() == f"__Host-{CSRF_COOKIE_BASE}"


@pytest.mark.asyncio
async def test_csrf_cookie_insecure_mode_set_cookie_is_plain_no_secure(
    raw_client: AsyncClient, monkeypatch
):
    """Default mode: the CSRF Set-Cookie uses the plain name and is NOT Secure."""
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    resp = await raw_client.get("/login")
    csrf_lines = [
        h for h in resp.headers.get_list("set-cookie") if h.startswith("revue_csrf=")
    ]
    assert csrf_lines, "GET must set the plain CSRF cookie in insecure mode"
    line = csrf_lines[0]
    assert "Secure" not in line
    assert "__Host-" not in line


@pytest.mark.asyncio
async def test_csrf_cookie_secure_mode_is_host_prefixed_secure(monkeypatch):
    """Secure mode: the CSRF Set-Cookie is ``__Host-`` prefixed, Secure, Path=/,
    NO Domain (the browser requirement), and stays non-httponly (double-submit
    needs the page to read it)."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    from auth import reset_serializer
    reset_serializer()
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.get("/login")
    csrf_lines = [
        h for h in resp.headers.get_list("set-cookie")
        if h.startswith("__Host-revue_csrf=")
    ]
    all_set_cookie = resp.headers.get_list("set-cookie")
    assert csrf_lines, f"expected __Host- CSRF cookie, got {all_set_cookie!r}"
    line = csrf_lines[0]
    assert "Secure" in line
    assert "Path=/" in line
    assert "Domain=" not in line  # __Host- forbids Domain
    assert "HttpOnly" not in line  # CSRF cookie must stay readable by the page
    # Guard against the removed-alias footgun: in secure mode NO code path may
    # set the plain insecure CSRF cookie name.
    assert not any(h.startswith("revue_csrf=") for h in all_set_cookie), (
        f"secure mode must NOT emit a plain revue_csrf cookie, got {all_set_cookie!r}"
    )


@pytest.mark.asyncio
async def test_csrf_round_trip_in_secure_mode(monkeypatch):
    """Set then read the ``__Host-`` CSRF cookie over https, and a matched POST
    passes CSRF — proving the set name and the read name agree in secure mode."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    from auth import reset_serializer
    reset_serializer()
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        get_resp = await ac.get("/signup")
        token = get_resp.cookies.get(csrf_cookie_name())
        assert token, "secure-mode GET must set the __Host- CSRF cookie"
        resp = await ac.post(
            "/signup",
            data={
                "email": "csrf-secure-rt@test.com",
                "password": "password1",
                CSRF_FORM_FIELD: token,
            },
            follow_redirects=False,
        )
    assert resp.status_code != 403, "matched __Host- CSRF token must pass in secure mode"


@pytest.mark.asyncio
async def test_csrf_post_without_token_still_403_in_secure_mode(monkeypatch):
    """Enforcement is unchanged in secure mode: a protected POST with no token
    still 403s (the hardening is about the cookie name/flags, not the gate)."""
    monkeypatch.setenv("COOKIE_SECURE", "1")
    from auth import reset_serializer
    reset_serializer()
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login",
            data={"email": "x@test.com", "password": "password1"},
            follow_redirects=False,
        )
    assert resp.status_code == 403

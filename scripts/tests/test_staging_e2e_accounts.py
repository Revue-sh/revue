"""Unit tests for the pure parts of scripts/staging_e2e_accounts.py.

Only the side-effect-free surface is tested here:
  * the canonical STATE constants + their groupings,
  * ``email_for`` derivation (stable, state-scoped, domain-driven),
  * the licence-key extraction regex applied to fixture HTML,
  * ``resolve_account_key`` with httpx mocked (login + page read), including the
    clear, account-naming error when no key is present.

No network: every test that touches ``resolve_account_key`` mocks httpx.
"""
from __future__ import annotations

import pytest

import staging_e2e_accounts as accts


# ---------------------------------------------------------------------------
# STATE constants — single source of truth
# ---------------------------------------------------------------------------

def test_state_constants_have_canonical_values():
    assert accts.STATE_ACTIVE_PRO == "ACTIVE_PRO"
    assert accts.STATE_ACTIVE_INDIE == "ACTIVE_INDIE"
    assert accts.STATE_FREE == "FREE"
    assert accts.STATE_LAPSED == "LAPSED"
    assert accts.STATE_NOT_ACTIVATED == "NOT_ACTIVATED"


def test_required_and_optional_state_groupings():
    assert accts.REQUIRED_STATES == [
        "ACTIVE_PRO",
        "ACTIVE_PRO_RENEWAL",
        "ACTIVE_INDIE",
        "FREE",
        "LAPSED",
    ]
    assert accts.OPTIONAL_STATES == ["NOT_ACTIVATED"]
    assert accts.ALL_STATES == accts.REQUIRED_STATES + accts.OPTIONAL_STATES


# ---------------------------------------------------------------------------
# email_for — pure derivation
# ---------------------------------------------------------------------------

def test_email_for_is_state_scoped_and_lowercased():
    assert accts.email_for("LAPSED", "revue-e2e.test") == "e2e-lapsed@revue-e2e.test"
    assert accts.email_for("ACTIVE_PRO", "example.org") == "e2e-active_pro@example.org"


def test_email_for_is_stable_across_calls():
    a = accts.email_for(accts.STATE_FREE, "revue-e2e.test")
    b = accts.email_for(accts.STATE_FREE, "revue-e2e.test")
    assert a == b == "e2e-free@revue-e2e.test"


# ---------------------------------------------------------------------------
# Licence-key extraction regex
# ---------------------------------------------------------------------------

_KEY = "lic_" + "a1b2c3d4" * 4  # lic_ + 32 hex chars


def test_extract_licence_key_finds_lic_token_in_html():
    html = f'<code class="cmd">revue activate {_KEY}</code>'
    assert accts.extract_licence_key(html) == _KEY


def test_extract_licence_key_returns_none_when_absent():
    assert accts.extract_licence_key("<p>no key on this page</p>") is None


def test_extract_licence_key_ignores_uppercase_hex_noise():
    # The key alphabet is lowercase hex; an uppercase blob must not match.
    assert accts.extract_licence_key("lic_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4") is None


# ---------------------------------------------------------------------------
# resolve_account_key — login + page read (httpx mocked)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeClient:
    """Minimal httpx.Client stand-in recording calls and serving canned pages."""

    def __init__(self, *, pages: dict, **_kw) -> None:
        self._pages = pages
        self.posts: list[tuple] = []
        self.gets: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def post(self, path, data=None, **_kw):
        self.posts.append((path, data))
        return _FakeResp("", 200)

    def get(self, path, **_kw):
        self.gets.append(path)
        return _FakeResp(self._pages.get(path, ""))


def _install_fake_httpx(monkeypatch, pages: dict) -> "list[_FakeClient]":
    created: list[_FakeClient] = []

    class _FakeHttpx:
        class HTTPError(Exception):
            pass

        @staticmethod
        def Client(**kw):
            c = _FakeClient(pages=pages, **kw)
            created.append(c)
            return c

    import staging_e2e_accounts as mod

    monkeypatch.setattr(mod, "_httpx", lambda: _FakeHttpx, raising=True)
    return created


def test_resolve_account_key_logs_in_then_reads_onboarding(monkeypatch):
    pages = {"/onboarding": f"<code>revue activate {_KEY}</code>"}
    created = _install_fake_httpx(monkeypatch, pages)

    key = accts.resolve_account_key(
        "https://staging.revue.sh", "e2e-free@revue-e2e.test", "pw"
    )

    assert key == _KEY
    client = created[0]
    assert ("/login", {"email": "e2e-free@revue-e2e.test", "password": "pw"}) in client.posts
    assert "/onboarding" in client.gets


def test_resolve_account_key_falls_back_to_dashboard(monkeypatch):
    pages = {"/onboarding": "<p>nothing here</p>", "/dashboard": f"key {_KEY}"}
    created = _install_fake_httpx(monkeypatch, pages)

    key = accts.resolve_account_key(
        "https://staging.revue.sh", "e2e-free@revue-e2e.test", "pw"
    )

    assert key == _KEY
    assert "/dashboard" in created[0].gets


def test_resolve_account_key_raises_naming_the_account_when_no_key(monkeypatch):
    pages = {"/onboarding": "<p>empty</p>", "/dashboard": "<p>empty</p>"}
    _install_fake_httpx(monkeypatch, pages)

    with pytest.raises(RuntimeError) as exc:
        accts.resolve_account_key(
            "https://staging.revue.sh", "e2e-lapsed@revue-e2e.test", "pw"
        )
    msg = str(exc.value)
    assert "e2e-lapsed@revue-e2e.test" in msg  # names the offending account
    assert "/onboarding" in msg or "onboarding" in msg


# ---------------------------------------------------------------------------
# CSRF token extraction + GET-then-POST submission (double-submit cookie)
# ---------------------------------------------------------------------------

_CSRF_TOKEN = "signed.csrf.token-VALUE_42"
_LOGIN_HTML_WITH_CSRF = (
    '<form method="post" action="/login">'
    f'<input type="hidden" name="csrf_token" value="{_CSRF_TOKEN}">'
    '<input name="email"><input name="password"></form>'
)


def test_extract_csrf_token_reads_hidden_field_value():
    assert accts.extract_csrf_token(_LOGIN_HTML_WITH_CSRF) == _CSRF_TOKEN


def test_extract_csrf_token_returns_none_when_field_absent():
    assert accts.extract_csrf_token("<form><input name='email'></form>") is None


def test_csrf_form_post_gets_page_then_submits_token_as_form_field(monkeypatch):
    """The POST body must carry the EXACT csrf_token value rendered into the GET
    page's hidden field — that is what the double-submit guard compares against
    the cookie (src/web/csrf.py). httpx's jar auto-sends the cookie; we supply
    the matching field."""
    _install_fake_httpx(monkeypatch, {"/login": _LOGIN_HTML_WITH_CSRF})
    httpx = accts._httpx()
    with httpx.Client() as client:  # the fake client
        resp = accts.csrf_form_post(
            client, "/login", "/login", {"email": "e@x.test", "password": "pw"}
        )
    assert resp.status_code == 200
    # GET happened first (minted the cookie + rendered the token).
    assert "/login" in client.gets
    # POST body echoes the rendered token under the csrf_token field.
    post_path, post_data = client.posts[-1]
    assert post_path == "/login"
    assert post_data["csrf_token"] == _CSRF_TOKEN
    assert post_data["email"] == "e@x.test"


def test_csrf_form_post_omits_token_when_page_has_no_field(monkeypatch):
    """If the GET renders no csrf field (e.g. an authenticated redirect), the POST
    is still attempted but carries NO csrf_token key — the caller decides."""
    _install_fake_httpx(monkeypatch, {"/login": "<p>no form here</p>"})
    httpx = accts._httpx()
    with httpx.Client() as client:
        accts.csrf_form_post(client, "/login", "/login", {"email": "e@x.test"})
    _post_path, post_data = client.posts[-1]
    assert "csrf_token" not in post_data


def test_resolve_account_key_submits_csrf_token_on_login(monkeypatch):
    """resolve_account_key's login POST must carry the csrf_token read from the
    /login page, or the double-submit guard 403s the form and login never lands."""
    pages = {
        "/login": _LOGIN_HTML_WITH_CSRF,
        "/onboarding": f"<code>revue activate {_KEY}</code>",
    }
    created = _install_fake_httpx(monkeypatch, pages)

    key = accts.resolve_account_key(
        "https://staging.revue.sh", "e2e-free@revue-e2e.test", "pw"
    )

    assert key == _KEY
    client = created[0]
    login_posts = [d for p, d in client.posts if p == "/login"]
    assert login_posts and login_posts[0]["csrf_token"] == _CSRF_TOKEN
    assert login_posts[0]["email"] == "e2e-free@revue-e2e.test"


# ---------------------------------------------------------------------------
# Signed synthetic webhooks — signature scheme, event builders, emit
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402

_WHSEC = "whsec_unit_test_secret"


def test_sign_webhook_format_and_value():
    """sign_webhook returns ``t=<ts>,v1=<64-hex>`` and the v1 is HMAC-SHA256 of
    ``"{ts}.{payload}"`` under the secret used verbatim as the key."""
    payload = '{"hello":"world"}'
    ts = 1_700_000_000
    header = accts.sign_webhook(payload, _WHSEC, ts)
    assert header.startswith(f"t={ts},v1=")
    v1 = header.split("v1=", 1)[1]
    assert len(v1) == 64 and all(c in "0123456789abcdef" for c in v1)
    expected = hmac.new(
        _WHSEC.encode(), f"{ts}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    assert v1 == expected


def test_sign_webhook_is_deterministic_for_same_inputs():
    p, ts = '{"a":1}', 123
    assert accts.sign_webhook(p, _WHSEC, ts) == accts.sign_webhook(p, _WHSEC, ts)


def test_synthetic_customer_id_is_stable_and_state_scoped():
    a = accts.synthetic_customer_id(accts.STATE_LAPSED)
    assert a == "cus_e2e_lapsed"
    assert a == accts.synthetic_customer_id(accts.STATE_LAPSED)  # stable
    assert a != accts.synthetic_customer_id(accts.STATE_ACTIVE_PRO)


def _events(state):
    return accts.build_subscription_events(
        state, user_id="77", price_pro="price_pro", price_indie="price_indie"
    )


def test_build_events_free_and_not_activated_emit_nothing():
    assert _events(accts.STATE_FREE) == []
    assert _events(accts.STATE_NOT_ACTIVATED) == []


def test_build_events_active_pro_null_omits_period_end():
    evs = _events(accts.STATE_ACTIVE_PRO)
    assert len(evs) == 1
    obj = evs[0]["data"]["object"]
    assert evs[0]["type"] == "customer.subscription.created"
    assert obj["status"] == "active"
    assert obj["items"]["data"][0]["price"]["id"] == "price_pro"
    assert obj["metadata"]["user_id"] == "77"
    assert obj["customer"] == "cus_e2e_active_pro"
    # The NULL-variant carries NO current_period_end → billing writes NULL.
    assert "current_period_end" not in obj


def test_build_events_active_pro_renewal_carries_2099_period_end():
    evs = _events(accts.STATE_ACTIVE_PRO_RENEWAL)
    assert len(evs) == 1
    obj = evs[0]["data"]["object"]
    assert obj["status"] == "active"
    assert obj["items"]["data"][0]["price"]["id"] == "price_pro"
    assert obj["current_period_end"] == accts.RENEWAL_EPOCH_2099


def test_build_events_active_indie_uses_indie_price():
    evs = _events(accts.STATE_ACTIVE_INDIE)
    assert len(evs) == 1
    assert evs[0]["data"]["object"]["items"]["data"][0]["price"]["id"] == "price_indie"


def test_build_events_lapsed_is_active_then_past_due_on_one_customer():
    """LAPSED = TWO ordered events on a STABLE customer id: created+active+PRO
    THEN updated+past_due. The account must be Pro first so the lapse branch
    retains the tier (billing.py 419-432)."""
    evs = _events(accts.STATE_LAPSED)
    assert len(evs) == 2
    first, second = evs
    assert first["type"] == "customer.subscription.created"
    assert first["data"]["object"]["status"] == "active"
    assert second["type"] == "customer.subscription.updated"
    assert second["data"]["object"]["status"] == "past_due"
    # Same stable customer id across both so event 2 finds the linked user.
    assert (first["data"]["object"]["customer"]
            == second["data"]["object"]["customer"]
            == "cus_e2e_lapsed")
    # Both PRO-priced (tier retained on lapse).
    for ev in evs:
        assert ev["data"]["object"]["items"]["data"][0]["price"]["id"] == "price_pro"


class _WebhookResp:
    def __init__(self, status_code=200, result="upgraded:user=1:tier=pro"):
        self.status_code = status_code
        self._result = result
        self.text = json.dumps({"status": "ok", "result": result})

    def json(self):
        return {"status": "ok", "result": self._result}


class _WebhookClient:
    """Records the content+headers of the /webhooks/stripe POST."""

    def __init__(self, *, resp=None):
        self.calls = []
        self._resp = resp or _WebhookResp()

    def post(self, path, content=None, headers=None, **_kw):
        self.calls.append({"path": path, "content": content, "headers": headers})
        return self._resp


def test_emit_subscription_event_signs_exact_bytes_and_posts():
    """emit signs the EXACT json bytes it POSTs (byte-exact) and sends them as the
    body with the Stripe-Signature header; /webhooks/stripe needs no csrf token."""
    event = {"type": "customer.subscription.created", "data": {"object": {"x": 1}}}
    client = _WebhookClient()
    accts.emit_subscription_event("https://app.test", _WHSEC, event, client=client)

    call = client.calls[0]
    assert call["path"] == "/webhooks/stripe"
    # The body sent is the verbatim json.dumps of the event (not re-serialised).
    assert call["content"] == json.dumps(event)
    assert call["headers"]["Content-Type"] == "application/json"
    sig = call["headers"]["Stripe-Signature"]
    # The signature verifies against the EXACT bytes posted (recompute the HMAC
    # over the same t and payload).
    ts = int(sig.split(",", 1)[0].split("t=", 1)[1])
    v1 = sig.split("v1=", 1)[1]
    expected = hmac.new(
        _WHSEC.encode(), f"{ts}.{call['content']}".encode(), hashlib.sha256
    ).hexdigest()
    assert v1 == expected
    # No csrf token is attached to the webhook POST (the endpoint is exempt).
    assert "csrf_token" not in (call["content"] or "")


def test_emit_subscription_event_rejects_non_200():
    client = _WebhookClient(resp=_WebhookResp(status_code=400, result=""))
    with pytest.raises(AssertionError) as exc:
        accts.emit_subscription_event("https://app.test", _WHSEC, {"a": 1},
                                      client=client)
    assert "400" in str(exc.value)


def test_emit_subscription_event_rejects_skipped_result_noop():
    """A 200 with result='skipped:unknown_price' is a NO-OP — must fail loud, not
    pass as a false green."""
    client = _WebhookClient(resp=_WebhookResp(result="skipped:unknown_price:price_x"))
    with pytest.raises(AssertionError) as exc:
        accts.emit_subscription_event("https://app.test", _WHSEC, {"a": 1},
                                      client=client)
    assert "NO-OP" in str(exc.value) or "skipped" in str(exc.value)


# ---------------------------------------------------------------------------
# Per-event result-prefix expectation (Edge F1) — derived from the event status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        ("active", "upgraded"),
        ("trialing", "upgraded"),
        ("past_due", "lapsed"),
        ("unpaid", "lapsed"),
        ("canceled", "downgraded"),
        ("incomplete", None),   # no_change → no definite prefix
        (None, None),           # missing status → None
    ],
)
def test_expected_result_prefix_from_event_status(status, expected):
    obj = {"customer": "c", "metadata": {"user_id": "1"}}
    if status is not None:
        obj["status"] = status
    event = accts._event("customer.subscription.updated", obj)
    assert accts.expected_result_prefix(event) == expected


def _active_event():
    return accts._event(
        "customer.subscription.created",
        {"customer": "cus_x", "status": "active", "metadata": {"user_id": "1"},
         "items": {"data": [{"price": {"id": "price_pro"}}]}},
    )


def _past_due_event():
    return accts._event(
        "customer.subscription.updated",
        {"customer": "cus_x", "status": "past_due", "metadata": {"user_id": "1"},
         "items": {"data": [{"price": {"id": "price_pro"}}]}},
    )


def test_emit_active_event_requires_upgraded_result():
    """An active event whose billing result comes back 'lapsed' (a billing
    regression) must FAIL at the POST, not pass through to a later poll."""
    client = _WebhookClient(resp=_WebhookResp(result="lapsed:user=1:status=past_due"))
    with pytest.raises(AssertionError) as exc:
        accts.emit_subscription_event("https://app.test", _WHSEC, _active_event(),
                                      client=client)
    assert "upgraded" in str(exc.value)


def test_emit_past_due_event_requires_lapsed_result():
    """The LAPSED second event (past_due) that erroneously comes back 'upgraded'
    is the exact regression the per-event check must surface at emit time."""
    client = _WebhookClient(resp=_WebhookResp(result="upgraded:user=1:tier=pro"))
    with pytest.raises(AssertionError) as exc:
        accts.emit_subscription_event("https://app.test", _WHSEC, _past_due_event(),
                                      client=client)
    assert "lapsed" in str(exc.value)


def test_emit_past_due_event_accepts_lapsed_result():
    """The correct past_due → lapsed result passes (derived prefix matches)."""
    client = _WebhookClient(resp=_WebhookResp(result="lapsed:user=1:status=past_due"))
    body = accts.emit_subscription_event("https://app.test", _WHSEC,
                                         _past_due_event(), client=client)
    assert body["result"].startswith("lapsed")


def test_emit_explicit_expected_prefix_none_opts_out_of_prefix_check():
    """expected_prefix=None keeps only the skipped-no-op contract (no prefix
    assertion) — e.g. for an event with an indefinite status."""
    client = _WebhookClient(resp=_WebhookResp(result="upgraded:user=1:tier=pro"))
    # A past_due event would normally require 'lapsed'; opting out accepts anything
    # non-skipped.
    accts.emit_subscription_event("https://app.test", _WHSEC, _past_due_event(),
                                  client=client, expected_prefix=None)


def test_sign_webhook_round_trips_through_real_stripe_library():
    """The discriminating proof: a real ``stripe.Webhook.construct_event`` accepts
    our signed payload with a near-now timestamp (300s tolerance). If this passes,
    both the byte handling AND the HMAC scheme are correct. Skipped where the
    stripe SDK is absent (bare scripts/ venv)."""
    stripe = pytest.importorskip("stripe")
    import time as _time

    # Use the real event builder so the load-bearing top-level envelope
    # (object="event", id) is present — construct_event reads event.object.
    event = accts._event(
        "customer.subscription.created", {"id": "sub_x", "status": "active"}
    )
    payload = json.dumps(event)
    ts = int(_time.time())
    sig = accts.sign_webhook(payload, _WHSEC, ts)
    # The whole point: constructing the event must NOT raise
    # SignatureVerificationError — that proves both the byte handling AND the HMAC
    # scheme. (The returned StripeObject's attribute/subscript access is quirky
    # under stripe-python v15 — billing_routes.py works around it by re-parsing the
    # raw bytes — so we assert on the verified envelope, not via .type access.)
    verified = stripe.Webhook.construct_event(payload, sig, _WHSEC)
    assert verified is not None
    assert verified.type == "customer.subscription.created"
    # A tampered signature MUST be rejected (negative control).
    with pytest.raises(stripe.SignatureVerificationError):
        stripe.Webhook.construct_event(payload, sig.replace("v1=", "v1=deadbeef"),
                                       _WHSEC)

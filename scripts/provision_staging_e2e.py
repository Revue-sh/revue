#!/usr/bin/env python3
"""Idempotent ENSURE-EXISTS provisioner for the REVUE-409 staging-E2E accounts.

A dedicated ``Provision → Staging E2E accounts`` pipeline step runs this script
(NOT --dry-run) BEFORE the ``E2E → Staging`` Playwright step. Its only job is to
make sure each licence STATE the suite exercises has a live staging account in
the right state. It does NOT hand keys to the maintainer and emits NO secrets.

The account model (LOCKED — REVUE-409 rework):

  * Accounts share ONE password (``STAGING_E2E_PASSWORD``) and a DERIVED e-mail
    (``e2e-<state>@<domain>``, see ``staging_e2e_accounts.email_for``). There are
    NO per-state ``STAGING_E2E_<STATE>_*`` secrets.
  * Licence KEYS are read back at RUNTIME by the E2E conftest
    (``staging_e2e_accounts.resolve_account_key``) — never stored as a secret.
  * Provisioning is idempotent ensure-exists using ONLY the existing
    signup/login/activate flows (there is no account-delete / reset endpoint;
    delete+recreate is intentionally out of scope).

Wipe recovery: just re-run the main pipeline. The provision step re-ensures the
accounts; the E2E step reads the keys live. No manual re-paste of secrets.

What it does, per state (idempotent — re-running detects an existing account by
logging in, and ensures its state rather than duplicating):

  FREE          signup → activate round-trip (stamp ``last_validated_at``) so the
                state resolves to free-validated, not not-activated.
  ACTIVE_INDIE  signup → activate round-trip → emit a SIGNED SYNTHETIC
                ``customer.subscription.created`` (Indie price, status=active)
                carrying ``metadata.user_id`` so the REAL staging webhook upgrades
                the account exactly as a live delivery would.
  ACTIVE_PRO    as ACTIVE_INDIE but Pro price, with NO ``current_period_end`` →
                billing writes NULL (the migration-reality variant).
  ACTIVE_PRO_RENEWAL  as ACTIVE_PRO but WITH a fixed ``current_period_end``
                (2099-12-31) so the renewal-date-rendering test runs on staging.
  LAPSED        emit TWO events on a stable synthetic customer id: active+Pro
                first (sets tier + links customer), then a ``past_due`` update so
                the webhook flips ``is_active=False`` with the tier retained.
  NOT_ACTIVATED (optional) signup only — NO activate round-trip, so the key stays
                never-validated. Not required by the current suite (the
                ``logged_in_page`` fixture reaches not-activated via fresh signup).

FAITHFULNESS — signed synthetic webhooks (no live Stripe)
---------------------------------------------------------
The load-bearing linkage in ``src/web/billing.py`` is ``metadata.user_id``: the
webhook (``process_webhook_event``) links the Stripe customer to the user via that
id (and the customer→user fallback at billing.py ~365). Rather than create live
Stripe objects, this script POSTs HMAC-signed synthetic ``customer.subscription.*``
events straight to ``/webhooks/stripe`` — the SAME endpoint a real delivery hits.
The app verifies the ``Stripe-Signature`` against ``STRIPE_WEBHOOK_SECRET``
(``stripe.Webhook.construct_event``) and runs the real linkage/tier logic, so the
exercised path is identical to production while ZERO Stripe objects are created.
The signing/emit/event-builder live in ``staging_e2e_accounts`` (dependency-light).

WHY LAPSED IS past_due, NOT cancel — load-bearing
-------------------------------------------------
``src/web/billing.py`` maps a CANCELLED subscription to the **free** state, NOT
lapsed (``customer.subscription.deleted`` → free; status ``canceled`` → free via
``_SUBSCRIPTION_STATUS_STATE``). The LAPSED state (``is_active=False`` with the
tier RETAINED) is reachable ONLY from a ``past_due`` / ``unpaid`` status. So the
LAPSED account is driven active+Pro first, then a ``past_due`` update event.

HARD CONSTRAINTS
----------------
* No secrets/keys are hardcoded. Everything comes from env (see ``Config``).
* ``--dry-run`` performs NO network calls and prints the planned action sequence
  WITHOUT any secret values — safe to run anywhere, including CI logs.
* This script emits NO secrets file: keys are read at runtime by the E2E suite,
  never pasted into Bitbucket. There is NO Stripe API key. The only secrets it
  CONSUMES are the shared ``STAGING_E2E_PASSWORD`` + ``STRIPE_WEBHOOK_SECRET`` +
  the price ids (all from env).

This module is provisioning TOOLING, not a test. Its pure plan-builder is unit
tested in ``scripts/tests/test_provision_staging_e2e.py``; the live execution
runs as the dedicated provision pipeline step against staging.

Usage
-----
    # See the plan without touching anything (no network, no secrets printed):
    python3 scripts/provision_staging_e2e.py --dry-run

    # Provision/reset all required states for real (needs the env below):
    python3 scripts/provision_staging_e2e.py

    # A single state:
    python3 scripts/provision_staging_e2e.py --state ACTIVE_PRO

Required env for a live run (NOT for --dry-run)
-----------------------------------------------
    STAGING_BASE_URL          e.g. https://staging.revue.sh
    STRIPE_WEBHOOK_SECRET     whsec_...  (the HMAC signing secret — MUST match the
                              staging app's value, or events fail sig verification)
    STRIPE_PRICE_INDIE_MONTHLY  price_...  (staging price ids — MUST match the app
    STRIPE_PRICE_PRO_MONTHLY    price_...   so tier_from_price_id resolves)
    STAGING_E2E_PASSWORD      ONE shared password reused across all E2E-owned
                              accounts (no per-state password secrets).
Optional:
    STAGING_E2E_EMAIL_DOMAIN  default "revue-e2e.test"; account emails are
                              ``e2e-<state>@<domain>`` so they are clearly
                              E2E-owned and stable across re-runs.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

# Canonical STATE constants + e-mail derivation live in the shared helper — the
# single source of truth (also imported by src/web/tests/e2e/conftest.py). When
# run as ``python3 scripts/provision_staging_e2e.py`` the scripts/ dir is on
# sys.path; under pytest, scripts/tests/conftest.py puts it there.
from staging_e2e_accounts import (  # noqa: E402
    ALL_STATES,
    DEFAULT_EMAIL_DOMAIN,
    OPTIONAL_STATES,
    REQUIRED_STATES,
    STATE_ACTIVE_INDIE,
    STATE_ACTIVE_PRO,
    STATE_ACTIVE_PRO_RENEWAL,
    STATE_FREE,
    STATE_LAPSED,
    STATE_NOT_ACTIVATED,
    build_subscription_events,
    csrf_form_post,
    email_for,
    emit_subscription_event,
)


# ---------------------------------------------------------------------------
# Config (env-driven; no secrets hardcoded)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Resolved provisioning configuration. ``require_live`` decides whether the
    secret-bearing fields must be present (a live run) or may be absent (dry-run).
    """

    base_url: str
    email_domain: str
    # Secret-bearing — only required for a live run. No Stripe API key: the paid
    # states are driven by SIGNED SYNTHETIC WEBHOOKS, so only the webhook signing
    # secret + the price ids (carried in the event) are needed.
    webhook_secret: Optional[str] = None
    price_indie: Optional[str] = None
    price_pro: Optional[str] = None
    account_password: Optional[str] = None

    @staticmethod
    def from_env(env: dict, *, require_live: bool) -> "Config":
        base_url = (env.get("STAGING_BASE_URL") or "https://staging.revue.sh").rstrip("/")
        email_domain = env.get("STAGING_E2E_EMAIL_DOMAIN") or DEFAULT_EMAIL_DOMAIN
        cfg = Config(
            base_url=base_url,
            email_domain=email_domain,
            webhook_secret=env.get("STRIPE_WEBHOOK_SECRET"),
            price_indie=env.get("STRIPE_PRICE_INDIE_MONTHLY"),
            price_pro=env.get("STRIPE_PRICE_PRO_MONTHLY"),
            account_password=env.get("STAGING_E2E_PASSWORD"),
        )
        if require_live:
            cfg.validate_live()
        return cfg

    def validate_live(self) -> None:
        """Fail fast (naming the missing var) before any live action runs.

        No Stripe-key guard any more: we never call the Stripe API and hold no
        ``sk_*`` key. The only Stripe secret is ``STRIPE_WEBHOOK_SECRET`` (the
        HMAC signing secret), which must MATCH the staging app's value or the
        synthetic events fail signature verification with HTTP 400. The price ids
        must match the app's so ``tier_from_price_id`` resolves the tier.
        """
        missing = []
        if not self.webhook_secret:
            missing.append("STRIPE_WEBHOOK_SECRET")
        if not self.price_indie:
            missing.append("STRIPE_PRICE_INDIE_MONTHLY")
        if not self.price_pro:
            missing.append("STRIPE_PRICE_PRO_MONTHLY")
        if not self.account_password:
            missing.append("STAGING_E2E_PASSWORD")
        if missing:
            raise SystemExit(
                "Cannot run live: missing env var(s): " + ", ".join(missing) +
                ". (Use --dry-run to preview without these.)"
            )

    def email_for(self, state: str) -> str:
        """Stable, E2E-owned email per state (idempotent across re-runs).

        Thin delegate to the shared helper so the derivation has ONE definition.
        """
        return email_for(state, self.email_domain)


# ---------------------------------------------------------------------------
# Pure plan builder (no network, no secrets) — unit tested
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One planned provisioning step. ``detail`` is human-readable and must never
    contain a secret value — it is safe to print in --dry-run / CI logs."""

    kind: str       # signup | activate_roundtrip | emit_webhooks | none
    detail: str


@dataclass(frozen=True)
class StatePlan:
    state: str
    email: str
    actions: tuple[Action, ...]


# Human-readable webhook plan detail per state (dry-run only; never a secret). The
# actual events are built at execution time from staging_e2e_accounts. Keeping
# this as a registry (not an if/elif ladder) mirrors the no-platform-elif rule.
_WEBHOOK_PLAN_DETAIL = {
    STATE_ACTIVE_INDIE: "emit signed customer.subscription.created (Indie, active, "
                        "metadata.user_id) → real webhook upgrades the account",
    STATE_ACTIVE_PRO: "emit signed customer.subscription.created (Pro, active, NO "
                      "current_period_end → NULL) → real webhook upgrades the account",
    STATE_ACTIVE_PRO_RENEWAL: "emit signed customer.subscription.created (Pro, active, "
                              "current_period_end=2099-12-31) → upgrades + renewal date",
    STATE_LAPSED: "emit TWO signed events on a stable synthetic customer: "
                  "created (Pro, active) THEN updated (past_due) → is_active=False, "
                  "tier retained → LAPSED. (past_due, NOT cancel — cancel maps to "
                  "FREE in billing.py.)",
}


def build_state_plan(state: str, cfg: Config) -> StatePlan:
    """Build the ordered action plan for one state. PURE — no I/O, no secrets.

    The action sequence encodes the faithful flow:
      * every state signs up,
      * states needing ``last_validated_at`` run the activate round-trip,
      * paid states emit SIGNED SYNTHETIC webhooks carrying metadata.user_id,
      * LAPSED emits active+Pro then a past_due update (NOT cancel — see module
        docstring; cancel maps to free in billing.py).
    """
    email = cfg.email_for(state)
    actions: list[Action] = [
        Action("signup", f"POST {cfg.base_url}/signup as {email} (or log in if it exists; reset state)")
    ]

    if state == STATE_NOT_ACTIVATED:
        actions.append(
            Action("none", "skip activate round-trip — key stays never-validated (not-activated)")
        )
    else:
        actions.append(
            Action(
                "activate_roundtrip",
                f"POST {cfg.base_url}/api/v2/licence/activate then /api/v2/licence/validate "
                f"to stamp last_validated_at (resolves to free/active, not not-activated)",
            )
        )

    detail = _WEBHOOK_PLAN_DETAIL.get(state)
    if detail is not None:
        actions.append(Action("emit_webhooks", detail))

    return StatePlan(state=state, email=email, actions=tuple(actions))


def build_provision_plan(states: list[str], cfg: Config) -> list[StatePlan]:
    """Build the full ordered plan for the requested states. PURE."""
    return [build_state_plan(s, cfg) for s in states]


def render_plan(plans: list[StatePlan]) -> str:
    """Render the plan as a secret-free, human-readable string (dry-run output)."""
    lines: list[str] = []
    lines.append("Staging-E2E ensure-exists plan (no secret values shown):")
    lines.append("")
    for p in plans:
        lines.append(f"=== {p.state}  (account: {p.email}) ===")
        for i, a in enumerate(p.actions, 1):
            lines.append(f"  {i}. [{a.kind}] {a.detail}")
        lines.append("")
    lines.append(
        "Keys are read at RUNTIME by the E2E suite (no per-state secrets). "
        "Accounts share STAGING_E2E_PASSWORD; emails are e2e-<state>@<domain>."
    )
    lines.append(
        "Paid states are driven by SIGNED SYNTHETIC webhooks to /webhooks/stripe "
        "(no live Stripe; zero Stripe objects created)."
    )
    lines.append(
        "LAPSED is induced via a past_due update event, NOT a cancellation — "
        "cancel maps to FREE in src/web/billing.py."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live executor (network only — signed synthetic webhooks, NO Stripe API). Thin,
# side-effecting. Each step is small and logs its intent (never a secret value).
# ---------------------------------------------------------------------------

def _http_client():
    import httpx  # imported lazily so --dry-run needs no deps
    return httpx


def _execute_state(plan: StatePlan, cfg: Config, *, log: Callable[[str], None]) -> None:
    """Ensure one state's account exists in the right state against the app.

    Idempotent ensure-exists: signup-or-login, optional activate round-trip,
    optional SIGNED SYNTHETIC webhook emission for the paid states, then a
    verify-poll of /account/plan. Reads the licence key only to drive the activate
    round-trip — it is NEVER returned or stored (the E2E suite reads keys at
    runtime via staging_e2e_accounts.resolve_account_key). No Stripe API is
    touched; the upgrade path is exercised by POSTing HMAC-signed events to
    /webhooks/stripe (same endpoint a real delivery hits).
    """
    httpx = _http_client()
    password = cfg.account_password or ""
    email = plan.email
    log(f"[{plan.state}] ensuring {email}")

    with httpx.Client(base_url=cfg.base_url, timeout=30.0, follow_redirects=True) as client:
        # 1. Signup (idempotent: if the account exists, log in instead).
        licence_key = _signup_or_login(client, email, password, log=log)

        # 2. Activate round-trip to stamp last_validated_at (skip not-activated).
        if any(a.kind == "activate_roundtrip" for a in plan.actions):
            _activate_roundtrip(client, cfg, licence_key, log=log)

        # 3. Emit the signed synthetic subscription webhook(s) for the paid states.
        # LAPSED emits two events (active then past_due); the ORDER is preserved by
        # sequential synchronous POSTs, each asserting a non-skipped 200.
        if any(a.kind == "emit_webhooks" for a in plan.actions):
            user_id = _resolve_user_id(client, log=log)
            events = build_subscription_events(
                plan.state,
                user_id=user_id,
                price_pro=cfg.price_pro or "",
                price_indie=cfg.price_indie or "",
            )
            for i, event in enumerate(events, 1):
                log(f"  emit synthetic webhook {i}/{len(events)}: {event['type']}")
                emit_subscription_event(
                    cfg.base_url, cfg.webhook_secret or "", event, client=client
                )

        # 4. Verify-then-exit: poll /account/plan until the resolved state lands,
        # turning a webhook/linkage failure LOUD here rather than leaving the
        # account silently free for the E2E suite. The webhook POSTs are
        # SYNCHRONOUS (200 = billing already updated the DB), so EVERY state —
        # including LAPSED's past_due — converges; no state is skipped.
        _verify_state(client, plan.state, log=log)


# ---------------------------------------------------------------------------
# Verify-then-exit: poll the account's plan page until it reflects the resolved
# state (closes the webhook race; turns a linkage failure LOUD instead of leaving
# the account silently free). Cues reuse the strings the E2E suite already
# asserts (src/web/tests/e2e/test_account_plan_e2e.py).
# ---------------------------------------------------------------------------

# Per-state acceptance predicate over the RAW /account/plan body text. A state is
# "converged" when its predicate returns True. These mirror the strings the E2E
# suite asserts so the provisioner's gate and the suite agree on each state.
#
# Tier match is CASE-SENSITIVE on the exact badge string ("Pro" / "Indie"): a
# lowercased ``"pro" in body`` would spuriously match "profile"/"approve"/etc. on
# a real page, collapsing the Pro/Indie distinction and hiding a tier mixup. The
# "licence active" cue is matched case-insensitively (copy lowercased below) since
# its exact casing is cosmetic.
_PLAN_CONVERGED = {
    STATE_ACTIVE_PRO: lambda b, lo: "licence active" in lo and "Pro" in b,
    # ACTIVE_PRO_RENEWAL additionally renders the fixed renewal date the page
    # asserts (2099-12-31) — the variant a live Stripe sub could never produce.
    STATE_ACTIVE_PRO_RENEWAL: lambda b, lo: (
        "licence active" in lo and "Pro" in b and "2099-12-31" in b
    ),
    STATE_ACTIVE_INDIE: lambda b, lo: "licence active" in lo and "Indie" in b,
    # FREE renders the "Upgrade to Indie" CTA (account_plan.html) + a command-box
    # NEVER. Empirically (verified against the four rendered pages) the FREE page
    # is the ONLY one containing "upgrade to": ACTIVE has no upgrade CTA, and the
    # LAPSED page's "Downgrade to Free" does NOT contain "upgrade to". We still
    # explicitly exclude the LAPSED page ("subscription ended") and the not-
    # activated command-box ("revue activate") as defense against future template
    # text that might introduce a generic "upgrade" elsewhere.
    STATE_FREE: lambda b, lo: (
        "upgrade to" in lo
        and "subscription ended" not in lo
        and "revue activate" not in lo
    ),
    # LAPSED renders the Re-subscribe CTA and never the word "invalid"; is_active
    # is False with the tier retained. The synchronous past_due webhook means this
    # converges immediately — no skip.
    STATE_LAPSED: lambda b, lo: "re-subscribe" in lo and "invalid" not in lo,
    # NOT_ACTIVATED shows the activation command-box (`revue activate`).
    STATE_NOT_ACTIVATED: lambda b, lo: "revue activate" in lo,
}


def _verify_state(client, state: str, *, log, timeout: float = 60.0,
                  interval: float = 2.0) -> None:
    """Poll ``/account/plan`` until it reflects ``state``, or raise after timeout.

    Closes the webhook race: a Stripe-test subscription is upgraded by an async
    webhook, so the account may still render its OLD state immediately after the
    API call returns. Polling the authenticated plan page until the expected cue
    appears (a) waits out that race deterministically, and (b) makes a user_id /
    linkage failure fail LOUD here — naming the state that never converged —
    instead of silently leaving the account free for the E2E suite to trip over.

    Bounded by ``timeout``; ``time`` is imported lazily so --dry-run stays
    dependency-light. States with no predicate (none expected here) are accepted
    immediately.
    """
    import time

    predicate = _PLAN_CONVERGED.get(state)
    if predicate is None:
        return
    log(f"  verify: polling /account/plan until it reflects {state}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/account/plan").text or ""
        # Pass BOTH the raw body (case-sensitive tier-badge match) and a lowercased
        # copy (case-insensitive cue match) so predicates choose per-cue.
        if predicate(body, body.lower()):
            log(f"  verify: {state} converged")
            return
        time.sleep(interval)
    raise RuntimeError(_verify_timeout_message(state, timeout))


# States whose /account/plan cue requires the licence to be VALIDATED (the
# activate→validate round-trip stamped last_validated_at). LAPSED is excluded: it
# renders from is_active=False alone, independent of validation. A timeout for
# these states is just as likely an activate/JWT-signing/rate-limit failure as a
# webhook/linkage one, so the error must name both (a JWT-500 or 429 must not be
# misread as a webhook bug).
_VALIDATION_DEPENDENT_STATES = frozenset({
    STATE_ACTIVE_PRO, STATE_ACTIVE_PRO_RENEWAL, STATE_ACTIVE_INDIE, STATE_FREE,
})


def _verify_timeout_message(state: str, timeout: float) -> str:
    base = (
        f"Account did not converge to {state} within {timeout:.0f}s — the "
        f"/account/plan page never showed the expected {state} cue. "
    )
    if state in _VALIDATION_DEPENDENT_STATES:
        cause = (
            "Candidate causes: (a) the activate round-trip failed to stamp "
            "last_validated_at — activate/JWT-signing error (e.g. JWT_SIGNING_KEY "
            "unset → HTTP 500) or activation rate-limit (HTTP 429); or (b) a "
            "webhook/linkage failure (user_id not stamped, price id mismatch). "
            "Check the activate WARNING line above first."
        )
    else:
        cause = (
            "Likely a webhook/linkage failure (user_id not stamped, the synthetic "
            "event was rejected, or a price-id/secret mismatch)."
        )
    return base + cause + " See docs/runbooks/staging-e2e-account.md."


def _signup_or_login(client, email: str, password: str, *, log) -> str:
    """Sign up the account; if it already exists, log in. Return its licence key.

    Idempotency — verified against the real app (src/web/routes/auth_routes.py):
    a duplicate signup re-renders ``signup.html`` with HTTP **200** and the body
    text "An account with this email already exists." — it does NOT return 400 or
    409. So detection is by BODY TEXT, not status code. On a fresh signup the app
    303-redirects to /onboarding (followed by httpx), establishing the session.

    CSRF: both ``POST /signup`` and ``POST /login`` are form-encoded, non-exempt
    POSTs, so they are blocked with HTTP 403 unless the body carries the
    ``csrf_token`` that matches the ``revue_csrf`` cookie (double-submit;
    src/web/csrf.py). ``csrf_form_post`` GETs the page first to mint the cookie +
    read the rendered token, then POSTs it.
    """
    log("  signup (or login if the account already exists)")
    resp = csrf_form_post(
        client, "/signup", "/signup", {"email": email, "password": password}
    )
    # Duplicate signup → HTTP 200 re-rendering the form with this exact phrase
    # (auth_routes.signup_submit). A fresh signup redirects (200 after follow) to
    # /onboarding and does NOT contain it.
    if "already exists" in resp.text.lower():
        log("  account exists → logging in to read state")
        csrf_form_post(
            client, "/login", "/login", {"email": email, "password": password}
        )
    # The licence key is rendered into the authenticated onboarding/dashboard
    # page; read it from there. (Parser kept defensive — the maintainer verifies.)
    return _read_licence_key(client, log=log)


def _read_licence_key(client, *, log) -> str:
    """Read the authenticated user's licence key from the onboarding page.

    The activation command-box renders ``revue activate <lic_...>``; extract it
    via the shared helper's regex (single source). Used only to drive the
    activate round-trip — the key is never returned to the caller or stored.
    """
    from staging_e2e_accounts import extract_licence_key

    # /onboarding and /dashboard use get_license_for_user (is_active=1 filter)
    # which hides lapsed rows. /account/plan uses get_any_license_for_user
    # (unfiltered) and renders any_license.key unconditionally — the key is
    # readable there even for lapsed accounts.
    for path in ("/onboarding", "/dashboard", "/account/plan"):
        key = extract_licence_key(client.get(path).text)
        if key:
            log("  read licence key (value hidden)")
            return key
    raise RuntimeError(
        "Could not read the account's licence key from /onboarding, /dashboard, "
        "or /account/plan — verify the account state manually (see runbook)."
    )


# Synthetic machine fingerprint for the activate round-trip. The endpoint
# (src/web/routes/api_routes.py) validates it against ``^[a-zA-Z0-9_-]{1,128}$``;
# a stable E2E-owned value satisfies that and identifies these activations.
_E2E_FINGERPRINT = "e2e-provisioner-fingerprint"


def _activate_roundtrip(client, cfg: Config, licence_key: str, *, log) -> None:
    """Run the real activate→validate round-trip to stamp last_validated_at.

    The activate endpoint's payload is ``{key, machine_fingerprint}`` (NOT
    ``licence_key``) and the fingerprint must match ``^[a-zA-Z0-9_-]{1,128}$`` —
    verified against src/web/routes/api_routes.py::ActivateRequest. A wrong field
    name 422s and leaves the account NEVER-VALIDATED (not-activated), so the
    free/active states never converge. On success the response carries ``{jwt,
    tier}``; POSTing that jwt to /validate stamps ``last_validated_at`` (the
    validate handler writes it on a successful, still-active validation).
    """
    log("  activate round-trip → stamp last_validated_at")
    act = client.post(
        "/api/v2/licence/activate",
        json={"key": licence_key, "machine_fingerprint": _E2E_FINGERPRINT},
    )
    jwt = None
    try:
        jwt = act.json().get("jwt") or act.json().get("token")
    except Exception:  # noqa: BLE001 — defensive
        jwt = None
    if jwt:
        client.post("/api/v2/licence/validate", json={"jwt": jwt})
        log("  validate → last_validated_at stamped")
    else:
        # KEEP non-raising: a failed activate is benign for LAPSED (no validation
        # needed) and the verify-poll fails loud for states that DO need it. But
        # surface the real cause (JWT-signing 500 / 429 rate-limit / etc.) by
        # including the response body, so CI logs name it instead of a bare status.
        try:
            body = act.text[:300]
        except Exception:  # noqa: BLE001 — defensive; never let logging raise
            body = "<unreadable>"
        log(
            "  WARNING: activate returned no JWT (HTTP %s) — the account will stay "
            "not-activated. Body: %s" % (act.status_code, body)
        )


def _resolve_user_id(client, *, log) -> str:
    """Resolve the authenticated user's id for the Stripe metadata linkage.

    Faithful path: the app stamps metadata.user_id server-side in
    /billing/checkout, but completing hosted Checkout headlessly is impractical,
    so we surface the user_id and stamp it onto a direct test-mode subscription
    (same linkage the webhook uses — billing.py links customer→user ONLY by
    stripe_customer_id or metadata.user_id; there is no email fallback, so the id
    is REQUIRED).

    The id is read from the stable ``data-user-id`` marker on ``/onboarding`` —
    the SAME authenticated page ``_read_licence_key`` already fetches. The
    onboarding route passes ``session`` into the template, and
    ``onboarding.html`` renders ``<span data-user-id="{{ session.user_id }}"
    hidden></span>`` (see that template). A clear RuntimeError is raised if the
    marker is absent so a markup regression fails loudly rather than silently
    skipping the subscription.
    """
    import re

    resp = client.get("/onboarding")
    m = re.search(r'data-user-id="(\d+)"', resp.text)
    if m:
        log("  resolved user_id (from /onboarding)")
        return m.group(1)
    raise RuntimeError(
        "Could not resolve user_id from the /onboarding data-user-id marker — "
        "the authenticated session may be missing, or onboarding.html's "
        "`<span data-user-id=...>` marker was removed (see runbook)."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan and exit. No network, no Stripe, no secrets shown.",
    )
    parser.add_argument(
        "--state", choices=ALL_STATES, action="append", default=None,
        help="Provision only this state (repeatable). Default: all required states.",
    )
    parser.add_argument(
        "--include-optional", action="store_true",
        help="Also ensure the optional NOT_ACTIVATED account.",
    )
    return parser.parse_args(argv)


def _selected_states(args: argparse.Namespace) -> list[str]:
    if args.state:
        return list(dict.fromkeys(args.state))  # de-dup, preserve order
    states = list(REQUIRED_STATES)
    if args.include_optional:
        states += OPTIONAL_STATES
    return states


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    states = _selected_states(args)

    cfg = Config.from_env(dict(os.environ), require_live=not args.dry_run)
    plans = build_provision_plan(states, cfg)

    if args.dry_run:
        print(render_plan(plans))
        print("\n(dry-run: nothing was created or modified; no secrets printed.)")
        return 0

    for plan in plans:
        _execute_state(plan, cfg, log=lambda m: print(m, flush=True))

    print("")
    print("=" * 72)
    print("DONE. Accounts ensured. No secrets emitted — the E2E suite reads each")
    print("account's licence key at runtime (staging_e2e_accounts.resolve_account_key).")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

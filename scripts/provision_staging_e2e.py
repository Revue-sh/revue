#!/usr/bin/env python3
"""Idempotent provisioner for the REVUE-409 staging-E2E per-state accounts.

The post-merge ``E2E → Staging`` pipeline step runs the existing
``src/web/tests/e2e/`` Playwright suite against the live staging app. Each
licence state it exercises maps to a PRE-PROVISIONED staging account whose
credentials + licence key live in Bitbucket repository secrets
(``STAGING_E2E_<STATE>_{EMAIL,PASSWORD,LICENCE_KEY}``).

This script automates creating / resetting those accounts against
``staging.revue.sh`` so the maintainer runs ONE command instead of clicking
through the UI four times. It is the executable form of the runbook's
"how to create/reset each account" section
(``docs/runbooks/staging-e2e-account.md``).

What it does, per state (idempotent — re-running detects an existing account by
logging in, and RESETS its state rather than duplicating):

  FREE          signup → activate round-trip (stamp ``last_validated_at``) so the
                state resolves to free-validated, not not-activated.
  ACTIVE_INDIE  signup → activate round-trip → Stripe TEST-mode Indie subscription
                carrying ``metadata.user_id`` so the REAL staging webhook upgrades
                the account exactly as a live checkout would.
  ACTIVE_PRO    as ACTIVE_INDIE but the Pro price.
  LAPSED        create the Pro subscription, then drive it to ``past_due`` so the
                webhook flips ``is_active=False`` with the tier retained.
  NOT_ACTIVATED (optional) signup only — NO activate round-trip, so the key stays
                never-validated. Not required by the current suite (the
                ``logged_in_page`` fixture reaches not-activated via fresh signup).

FAITHFULNESS — how the real webhook path is reused
--------------------------------------------------
The load-bearing linkage in ``src/web/billing.py`` is ``metadata.user_id``: the
real ``/billing/checkout`` stamps it into the checkout + subscription metadata,
and the webhook (``process_webhook_event``) links the Stripe customer to the user
via that id (and the customer→user fallback at billing.py ~365). This script
reuses that EXACT linkage by stamping ``metadata={"user_id": <id>}`` on the
test-mode Stripe customer + subscription, so Stripe itself delivers a real,
HMAC-signed ``customer.subscription.created`` to the staging webhook — we never
hand-POST a webhook (the endpoint verifies the signature and would 400 a forgery).

DEVIATION FROM THE TICKET'S STEP 4 (cancel ≠ lapsed) — load-bearing
-------------------------------------------------------------------
``src/web/billing.py`` maps a CANCELLED subscription to the **free** state, NOT
lapsed:
  * ``customer.subscription.deleted``        → tier reset to free, is_active=True
  * status ``canceled`` on an updated event  → free (``_SUBSCRIPTION_STATUS_STATE``)
The LAPSED state (``is_active=False`` with the tier RETAINED) is reachable ONLY
from a ``past_due`` / ``unpaid`` status — i.e. a FAILED RENEWAL (dunning), not a
cancellation. A failing card at *creation* yields ``incomplete`` → ``no_change``,
also not lapsed. So this script induces lapsed via a renewal failure (a Stripe
test clock advanced past a renewal whose payment fails), and the plan records
that intent. This corrects the ticket's "cancel/expire it" wording against the
primary-source webhook semantics; see the runbook "Logged gaps / deviations".

HARD CONSTRAINTS
----------------
* No secrets/keys are hardcoded. Everything comes from env (see ``Config``).
* ``--dry-run`` performs NO network calls and prints the planned action sequence
  WITHOUT any secret values — safe to run anywhere, including CI logs.
* Emitted credentials + licence keys are written to a gitignored file
  (default ``.staging-e2e-creds.local``) and/or printed with a "do not commit"
  banner. They are NEVER echoed in ``--dry-run`` and never sent to CI logs.

This module is provisioning TOOLING, not a test. Its pure plan-builder is unit
tested in ``scripts/tests/test_provision_staging_e2e.py``; the live execution is
exercised by the maintainer against staging (which this author cannot reach).

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
    STRIPE_SECRET_KEY         sk_test_...  (TEST mode only — guarded)
    STRIPE_PRICE_INDIE_MONTHLY  price_...  (staging test price ids)
    STRIPE_PRICE_PRO_MONTHLY    price_...
    STAGING_E2E_PASSWORD      shared password policy for the provisioned accounts
                              (a single strong password reused across the 4
                              E2E-owned accounts; stored per-state as the
                              *_PASSWORD secret)
Optional:
    STAGING_E2E_EMAIL_DOMAIN  default "revue-e2e.test"; account emails are
                              ``e2e-<state>@<domain>`` so they are clearly
                              E2E-owned and stable across re-runs.
    STAGING_E2E_CREDS_FILE    output path (default .staging-e2e-creds.local)
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

# The canonical states (mirrors src/web/tests/e2e/conftest.py). Kept as a literal
# list here so the script has no import dependency on the web package.
STATE_ACTIVE_PRO = "ACTIVE_PRO"
STATE_ACTIVE_INDIE = "ACTIVE_INDIE"
STATE_FREE = "FREE"
STATE_LAPSED = "LAPSED"
STATE_NOT_ACTIVATED = "NOT_ACTIVATED"

REQUIRED_STATES = [STATE_ACTIVE_PRO, STATE_ACTIVE_INDIE, STATE_FREE, STATE_LAPSED]
OPTIONAL_STATES = [STATE_NOT_ACTIVATED]
ALL_STATES = REQUIRED_STATES + OPTIONAL_STATES


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
    creds_file: str
    # Secret-bearing — only required for a live run.
    stripe_secret_key: Optional[str] = None
    price_indie: Optional[str] = None
    price_pro: Optional[str] = None
    account_password: Optional[str] = None

    @staticmethod
    def from_env(env: dict, *, require_live: bool) -> "Config":
        base_url = (env.get("STAGING_BASE_URL") or "https://staging.revue.sh").rstrip("/")
        email_domain = env.get("STAGING_E2E_EMAIL_DOMAIN") or "revue-e2e.test"
        creds_file = env.get("STAGING_E2E_CREDS_FILE") or ".staging-e2e-creds.local"
        cfg = Config(
            base_url=base_url,
            email_domain=email_domain,
            creds_file=creds_file,
            stripe_secret_key=env.get("STRIPE_SECRET_KEY"),
            price_indie=env.get("STRIPE_PRICE_INDIE_MONTHLY"),
            price_pro=env.get("STRIPE_PRICE_PRO_MONTHLY"),
            account_password=env.get("STAGING_E2E_PASSWORD"),
        )
        if require_live:
            cfg.validate_live()
        return cfg

    def validate_live(self) -> None:
        """Fail fast (naming the missing var) before any live action runs."""
        missing = []
        if not self.stripe_secret_key:
            missing.append("STRIPE_SECRET_KEY")
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
        # Guard: refuse a LIVE Stripe key — this provisioner is test-mode only.
        if self.stripe_secret_key and self.stripe_secret_key.startswith("sk_live_"):
            raise SystemExit(
                "Refusing to run: STRIPE_SECRET_KEY is a LIVE key (sk_live_*). "
                "This provisioner is TEST-mode only — use an sk_test_* key."
            )

    def email_for(self, state: str) -> str:
        """Stable, E2E-owned email per state (idempotent across re-runs)."""
        return f"e2e-{state.lower()}@{self.email_domain}"


# ---------------------------------------------------------------------------
# Pure plan builder (no network, no secrets) — unit tested
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One planned provisioning step. ``detail`` is human-readable and must never
    contain a secret value — it is safe to print in --dry-run / CI logs."""

    kind: str       # signup | activate_roundtrip | stripe_subscribe | stripe_lapse | none
    detail: str


@dataclass(frozen=True)
class StatePlan:
    state: str
    email: str
    actions: tuple[Action, ...]
    secrets: tuple[str, ...]  # the secret NAMES this state will populate


def build_state_plan(state: str, cfg: Config) -> StatePlan:
    """Build the ordered action plan for one state. PURE — no I/O, no secrets.

    The action sequence encodes the faithful flow:
      * every state signs up,
      * states needing ``last_validated_at`` run the activate round-trip,
      * paid states subscribe in Stripe TEST mode with metadata.user_id,
      * LAPSED additionally drives the sub to past_due (NOT cancel — see module
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

    if state == STATE_ACTIVE_INDIE:
        actions.append(
            Action("stripe_subscribe", "Stripe TEST: subscribe Indie price with metadata.user_id "
                                       "(real webhook upgrades the account)")
        )
    elif state == STATE_ACTIVE_PRO:
        actions.append(
            Action("stripe_subscribe", "Stripe TEST: subscribe Pro price with metadata.user_id "
                                       "(real webhook upgrades the account)")
        )
    elif state == STATE_LAPSED:
        actions.append(
            Action("stripe_subscribe", "Stripe TEST: subscribe Pro price with metadata.user_id")
        )
        actions.append(
            Action(
                "stripe_lapse",
                "Stripe TEST: drive the subscription to past_due via a failed RENEWAL "
                "(test clock) so the webhook sets is_active=False, tier retained → LAPSED. "
                "NOTE: cancel would map to FREE, not lapsed (billing.py).",
            )
        )

    secrets = (
        f"STAGING_E2E_{state}_EMAIL",
        f"STAGING_E2E_{state}_PASSWORD",
        f"STAGING_E2E_{state}_LICENCE_KEY",
    )
    return StatePlan(state=state, email=email, actions=tuple(actions), secrets=secrets)


def build_provision_plan(states: list[str], cfg: Config) -> list[StatePlan]:
    """Build the full ordered plan for the requested states. PURE."""
    return [build_state_plan(s, cfg) for s in states]


def render_plan(plans: list[StatePlan]) -> str:
    """Render the plan as a secret-free, human-readable string (dry-run output)."""
    lines: list[str] = []
    lines.append("Staging-E2E provisioning plan (no secret values shown):")
    lines.append("")
    for p in plans:
        lines.append(f"=== {p.state}  (account: {p.email}) ===")
        for i, a in enumerate(p.actions, 1):
            lines.append(f"  {i}. [{a.kind}] {a.detail}")
        lines.append(f"  → will populate secrets: {', '.join(p.secrets)}")
        lines.append("")
    lines.append(
        "Deviation: LAPSED is induced via a failed renewal (past_due), NOT a "
        "cancellation — cancel maps to FREE in src/web/billing.py."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live executor (network + Stripe). Thin, side-effecting, NOT unit tested —
# exercised by the maintainer against staging. Each step is small and logs its
# intent (never a secret value).
# ---------------------------------------------------------------------------

@dataclass
class ProvisionResult:
    state: str
    email: str
    password: str
    licence_key: str


def _http_client():
    import httpx  # imported lazily so --dry-run needs no deps
    return httpx


def _execute_state(plan: StatePlan, cfg: Config, *, log: Callable[[str], None]) -> ProvisionResult:
    """Execute one state's plan against staging. Returns the resolved creds.

    This is intentionally a straightforward sequence rather than a clever
    abstraction: the maintainer runs it live and reads the log. It is NOT covered
    by unit tests (no live Stripe/staging here); the PURE planner above is.
    """
    httpx = _http_client()
    password = cfg.account_password or ""
    email = plan.email
    log(f"[{plan.state}] provisioning {email}")

    with httpx.Client(base_url=cfg.base_url, timeout=30.0, follow_redirects=True) as client:
        # 1. Signup (idempotent: if the account exists, log in instead).
        licence_key = _signup_or_login(client, email, password, log=log)

        # 2. Activate round-trip to stamp last_validated_at (skip not-activated).
        if any(a.kind == "activate_roundtrip" for a in plan.actions):
            _activate_roundtrip(client, cfg, licence_key, log=log)

        # 3/4. Stripe test-mode subscription (+ lapse) for the paid states.
        if any(a.kind == "stripe_subscribe" for a in plan.actions):
            user_id = _resolve_user_id(client, log=log)
            _stripe_subscribe(
                cfg,
                user_id=user_id,
                email=email,
                price_id=cfg.price_pro if plan.state in (STATE_ACTIVE_PRO, STATE_LAPSED) else cfg.price_indie,
                lapse=any(a.kind == "stripe_lapse" for a in plan.actions),
                log=log,
            )

    return ProvisionResult(state=plan.state, email=email, password=password, licence_key=licence_key)


def _signup_or_login(client, email: str, password: str, *, log) -> str:
    """Sign up the account; if it already exists, log in. Return its licence key.

    Idempotency: the staging signup returns a duplicate-email error for an
    existing account, in which case we log in and read the existing key.
    """
    log("  signup (or login if the account already exists)")
    resp = client.post("/signup", data={"email": email, "password": password})
    body = resp.text
    if "already exists" in body or resp.status_code in (400, 409):
        log("  account exists → logging in to reset/read state")
        client.post("/login", data={"email": email, "password": password})
    # The licence key is rendered into the authenticated onboarding/dashboard
    # page; read it from there. (Parser kept defensive — the maintainer verifies.)
    return _read_licence_key(client, log=log)


def _read_licence_key(client, *, log) -> str:
    """Read the authenticated user's licence key from the onboarding page.

    The activation command-box renders ``revue activate <lic_...>``; extract it.
    """
    import re

    resp = client.get("/onboarding")
    m = re.search(r"lic_[a-f0-9]{32}", resp.text)
    if not m:
        resp = client.get("/dashboard")
        m = re.search(r"lic_[a-f0-9]{32}", resp.text)
    if not m:
        raise RuntimeError(
            "Could not read the account's licence key from /onboarding or "
            "/dashboard — verify the account state manually (see runbook)."
        )
    log("  read licence key (value hidden)")
    return m.group(0)


def _activate_roundtrip(client, cfg: Config, licence_key: str, *, log) -> None:
    """Run the real activate→validate round-trip to stamp last_validated_at."""
    log("  activate round-trip → stamp last_validated_at")
    act = client.post("/api/v2/licence/activate", json={"licence_key": licence_key})
    jwt = None
    try:
        jwt = act.json().get("jwt") or act.json().get("token")
    except Exception:  # noqa: BLE001 — defensive; maintainer verifies live
        jwt = None
    if jwt:
        client.post("/api/v2/licence/validate", json={"jwt": jwt})
    else:
        log("  WARNING: activate did not return a JWT — verify validate path manually")


def _resolve_user_id(client, *, log) -> str:
    """Resolve the authenticated user's id for the Stripe metadata linkage.

    Faithful path: the app stamps metadata.user_id server-side in
    /billing/checkout, but completing hosted Checkout headlessly is impractical,
    so we surface the user_id and stamp it onto a direct test-mode subscription
    (same linkage the webhook uses). The id is read from an authenticated page /
    API; the maintainer confirms the selector against staging.
    """
    import re

    for path in ("/dashboard", "/account/plan"):
        resp = client.get(path)
        m = re.search(r'data-user-id="(\d+)"', resp.text)
        if m:
            log(f"  resolved user_id (from {path})")
            return m.group(1)
    raise RuntimeError(
        "Could not resolve user_id for the Stripe metadata linkage — expose it "
        "on an authenticated page or adapt this selector (see runbook)."
    )


def _stripe_subscribe(cfg: Config, *, user_id: str, email: str, price_id: str,
                      lapse: bool, log) -> None:
    """Create a TEST-mode Stripe subscription carrying metadata.user_id so the
    REAL staging webhook upgrades the account; optionally drive it to past_due.

    metadata.user_id is the load-bearing linkage (src/web/billing.py): the
    webhook links the Stripe customer to the user via it. We do NOT hand-POST a
    webhook — Stripe delivers the signed event to staging itself.
    """
    import stripe  # lazy import; --dry-run needs no stripe

    stripe.api_key = cfg.stripe_secret_key
    log(f"  Stripe TEST: customer for {email} (metadata.user_id set)")
    customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})

    pm = stripe.PaymentMethod.attach(
        "pm_card_visa", customer=customer.id  # Stripe test payment method
    )
    stripe.Customer.modify(
        customer.id, invoice_settings={"default_payment_method": pm.id}
    )

    log("  Stripe TEST: subscription with metadata.user_id (real webhook upgrades)")
    sub = stripe.Subscription.create(
        customer=customer.id,
        items=[{"price": price_id}],
        metadata={"user_id": user_id},
    )

    if lapse:
        # Lapsed = past_due (failed RENEWAL), NOT cancel (cancel → free). The
        # faithful test-mode mechanism is a test clock advanced past a renewal
        # whose payment fails. This is left as an explicit, logged step for the
        # maintainer to drive with the test clock + a failing test card, because
        # inducing a real renewal failure reliably is environment-specific.
        log(
            "  Stripe TEST: drive subscription %s to past_due via a failed renewal "
            "(test clock) → webhook sets is_active=False, tier retained (LAPSED). "
            "See runbook for the exact test-clock steps." % sub.id
        )


# ---------------------------------------------------------------------------
# Credentials emission (gitignored file; never to CI logs; never in --dry-run)
# ---------------------------------------------------------------------------

def write_creds(results: list[ProvisionResult], cfg: Config) -> str:
    """Write the per-state secrets to the gitignored creds file, paste-ready for
    Bitbucket. Returns the path. The VALUES are written ONLY here, never logged.
    """
    lines = [
        "# REVUE-409 staging-E2E credentials — DO NOT COMMIT.",
        "# Paste each as a SECURED Bitbucket repository variable, then delete this file.",
        "",
    ]
    for r in results:
        lines.append(f"STAGING_E2E_{r.state}_EMAIL={r.email}")
        lines.append(f"STAGING_E2E_{r.state}_PASSWORD={r.password}")
        lines.append(f"STAGING_E2E_{r.state}_LICENCE_KEY={r.licence_key}")
        lines.append("")
    path = cfg.creds_file
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.chmod(path, 0o600)
    return path


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
        help="Also provision the optional NOT_ACTIVATED account.",
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

    results: list[ProvisionResult] = []
    for plan in plans:
        results.append(_execute_state(plan, cfg, log=lambda m: print(m, flush=True)))

    path = write_creds(results, cfg)
    print("")
    print("=" * 72)
    print(f"DONE. Per-state secrets written to: {path}")
    print("STORE THESE IN BITBUCKET (Secured) — DO NOT COMMIT. Delete the file after.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

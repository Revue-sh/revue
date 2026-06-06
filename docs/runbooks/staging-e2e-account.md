# Runbook — Staging E2E accounts (REVUE-409)

The post-merge **E2E → Staging** Pipelines step (`&e2e-web-staging` in
`bitbucket-pipelines.yml`) runs the existing `src/web/tests/e2e/` Playwright
suite against the live staging app (`https://staging.revue.sh`). Staging has **no
DB access**, so the local SQL seeding factory cannot run. Instead each licence
**state** the suite needs maps to a **pre-provisioned staging account**, selected
per test by the conftest fixtures (`_classify_state` → `_staging_account`).

This runbook is the authoritative source for: which accounts exist, the exact
repository secrets, how to create/reset each account, rotation, and dirty-state
recovery.

> **Maintainer action required before the first green main run.** The accounts
> and secrets below do not exist yet — the implementing branch cannot create
> them (account creation + Stripe-test licence states + secrets admin are the
> maintainer's responsibility). Until they are provisioned, the `E2E → Staging`
> step fails fast with the exact missing secret names. It is a **plain** (hard)
> step, so a red run halts the pipeline and the manual prod-promotion step never
> becomes available.

---

## 1. State → account → secret matrix

The fixtures derive a STATE from the seed call's parameters
(`tier` / `is_active` / `validated`) with this precedence:

1. `is_active == False` → **LAPSED** (checked first — lapsed tests pass `tier="pro"`)
2. `validated == False` → **NOT_ACTIVATED**
3. `tier == "free"` → **FREE**
4. `tier == "pro"` → **ACTIVE_PRO**
5. otherwise → **ACTIVE_INDIE**

| State | Account purpose | Required? | Secrets (Bitbucket repository variables, **secured**) |
|-------|-----------------|-----------|--------------------------------------------------------|
| `ACTIVE_PRO` | Active, validated, **Pro** tier; subscription live | **Yes** | `STAGING_E2E_ACTIVE_PRO_EMAIL`, `STAGING_E2E_ACTIVE_PRO_PASSWORD`, `STAGING_E2E_ACTIVE_PRO_LICENCE_KEY` |
| `ACTIVE_INDIE` | Active, validated, **Indie** tier | **Yes** | `STAGING_E2E_ACTIVE_INDIE_EMAIL`, `STAGING_E2E_ACTIVE_INDIE_PASSWORD`, `STAGING_E2E_ACTIVE_INDIE_LICENCE_KEY` |
| `FREE` | Validated **free** account (no paid licence) | **Yes** | `STAGING_E2E_FREE_EMAIL`, `STAGING_E2E_FREE_PASSWORD`, `STAGING_E2E_FREE_LICENCE_KEY` |
| `LAPSED` | Pro/Indie account whose subscription has **lapsed** (cancelled / inactive) | **Yes** | `STAGING_E2E_LAPSED_EMAIL`, `STAGING_E2E_LAPSED_PASSWORD`, `STAGING_E2E_LAPSED_LICENCE_KEY` |
| `NOT_ACTIVATED` | Never-validated key | **No (optional)** | `STAGING_E2E_NOT_ACTIVATED_EMAIL`, `STAGING_E2E_NOT_ACTIVATED_PASSWORD`, `STAGING_E2E_NOT_ACTIVATED_LICENCE_KEY` |

### Why two ACTIVE accounts

The reused suite asserts the rendered tier string: `test_active_pro_*` asserts
`"Pro"`, `test_active_indie_*` asserts `"Indie"`. A single active account cannot
satisfy both, so the active state is split into `ACTIVE_PRO` and `ACTIVE_INDIE`.

### Why `NOT_ACTIVATED` is optional

The not-activated test
(`test_account_plan_e2e.py::test_not_activated_freshly_signed_up_prefills_real_key`)
reaches the not-activated state via a **fresh signup** (the `logged_in_page`
fixture), which staging satisfies on every run without any pre-provisioned
account. The `NOT_ACTIVATED` secrets only become required if a future test seeds
`validated=False` through `seed_active_licence` / `seed_user_with_licence`. The
guard in `bitbucket-pipelines.yml` therefore does **not** require them; provision
them only if/when such a test is added.

### Secret-value contract (must hold exactly)

- `*_LICENCE_KEY` **must be the account's real licence key** as it renders on the
  account's pages. Several tests assert the rendered command-box key equals the
  key the fixture returns (`cmd == f"revue activate {identity['key']}"`,
  `clip == identity['key']`). If the secret drifts from the account's real key,
  these fail.
- `*_EMAIL` / `*_PASSWORD` must log in via the normal `/login` UI flow (the
  staging `auth_cookie` fixture performs a real UI login, not a cookie mint).

---

## 2. Automated provisioning (recommended) — `scripts/provision_staging_e2e.py`

Run ONE command instead of clicking through the UI four times. The script is
**idempotent** (re-running detects an existing account and resets its state
rather than duplicating) and is the executable form of the manual steps in §3.
The runbook's dirty-state recovery (§5) is "re-run this script".

### What it does, faithfully

- **Signup** the 4 accounts against the staging signup endpoint and captures
  each account's auto-issued licence key.
- **Activate round-trip** (`/api/v2/licence/activate` → `/api/v2/licence/validate`)
  for free + active states, to stamp `last_validated_at` so the state resolves to
  free/active rather than not-activated. NOT run for `NOT_ACTIVATED`.
- **Stripe TEST-mode subscription** for the active states, carrying
  `metadata.user_id` so the **real** staging webhook upgrades the account exactly
  as a live checkout would (the load-bearing customer↔workspace linkage in
  `src/web/billing.py`). The script does **not** hand-POST webhooks — the
  `/webhooks/stripe` endpoint verifies the HMAC signature, so Stripe itself
  delivers the signed event.

### ⚠️ Deviation: LAPSED is `past_due`, NOT cancel

`src/web/billing.py` maps a **cancelled** subscription to the **free** state
(`customer.subscription.deleted` → free; status `canceled` → free), not lapsed.
The **LAPSED** state (`is_active=False` with the tier retained) is reachable
ONLY from a `past_due` / `unpaid` status — a **failed renewal** (dunning). A
failing card at *creation* yields `incomplete` → `no_change`, also not lapsed.
The script therefore induces lapsed via a Stripe **test clock** advanced past a
renewal whose payment fails (the script prints this as an explicit step). This
corrects the original "cancel/expire it" wording against the primary-source
webhook semantics.

### How the maintainer runs it

```bash
# Preview the plan — no network, no Stripe, no secrets printed (safe anywhere):
python3 scripts/provision_staging_e2e.py --dry-run

# Provision/reset all 4 required states for real:
STAGING_BASE_URL=https://staging.revue.sh \
STRIPE_SECRET_KEY=sk_test_... \
STRIPE_PRICE_INDIE_MONTHLY=price_... \
STRIPE_PRICE_PRO_MONTHLY=price_... \
STAGING_E2E_PASSWORD='<strong shared password for the 4 E2E accounts>' \
python3 scripts/provision_staging_e2e.py

# A single state:
python3 scripts/provision_staging_e2e.py --state ACTIVE_PRO
# Optional NOT_ACTIVATED account:
python3 scripts/provision_staging_e2e.py --include-optional
```

Required env (live run only; `--dry-run` needs none): `STRIPE_SECRET_KEY`
(**sk_test_** only — the script refuses an `sk_live_` key), `STRIPE_PRICE_INDIE_MONTHLY`,
`STRIPE_PRICE_PRO_MONTHLY`, `STAGING_E2E_PASSWORD`. Optional:
`STAGING_BASE_URL` (default `https://staging.revue.sh`),
`STAGING_E2E_EMAIL_DOMAIN` (default `revue-e2e.test`; emails are
`e2e-<state>@<domain>`, stable across re-runs), `STAGING_E2E_CREDS_FILE`.

### Output → secrets

The script writes the per-state creds + keys to a **gitignored** file
(default `.staging-e2e-creds.local`, mode 0600) in paste-ready
`STAGING_E2E_<STATE>_<FIELD>=value` form. **Secret values are never printed to
stdout/CI logs and never appear in `--dry-run`.** Paste each as a **Secured**
Bitbucket repository variable (§4), then delete the file.

> The maintainer runs this against staging. The author cannot (no staging
> secrets); the script's pure plan-builder is unit-tested
> (`scripts/tests/test_provision_staging_e2e.py`), and `--dry-run` is validated.
> The live execution selectors (reading the rendered licence key / `user_id` from
> the authenticated pages) should be confirmed on the first live run and adapted
> if the staging markup differs.

---

## 3. Create / reset each account against `staging.revue.sh` (manual fallback)

If you cannot run the script, create the accounts manually. Use a controlled
mailbox (e.g. a `+e2e-<state>` alias) so the accounts are clearly E2E-owned and
never reused by another process during a pipeline run.

### Common steps (all states)

1. Sign up at `https://staging.revue.sh/signup` with the account email +
   password. Record both as the `*_EMAIL` / `*_PASSWORD` secrets.
2. After signup, the account is **free / not-activated** (a key exists but has
   never been validated).

### `FREE`

1. Sign up (common steps).
2. Log in once and trigger a licence validation so the state resolves to
   **free-validated** (not not-activated): run `revue activate <key>` against the
   account's key, or hit the validate flow once.
3. Record the account's licence key as `STAGING_E2E_FREE_LICENCE_KEY`.

### `ACTIVE_INDIE` / `ACTIVE_PRO`

1. Sign up (common steps).
2. Subscribe to the **Indie** (resp. **Pro**) plan using a **Stripe test** card
   (e.g. `4242 4242 4242 4242`). Confirm the dashboard shows the correct tier and
   "Licence active".
3. Validate once (`revue activate <key>`) so the "Last verified" line renders.
4. Record the account's licence key as `STAGING_E2E_ACTIVE_INDIE_LICENCE_KEY`
   (resp. `STAGING_E2E_ACTIVE_PRO_LICENCE_KEY`).

### `LAPSED`

1. Create as `ACTIVE_PRO`/`ACTIVE_INDIE` above (a paid, validated account).
2. **Cancel** the Stripe-test subscription so it lapses (subscription becomes
   `canceled` / inactive while the tier is preserved). The account page must show
   the lapsed (Re-subscribe / Downgrade) state and **never** the word "invalid".
3. Record the licence key as `STAGING_E2E_LAPSED_LICENCE_KEY`.

### Resetting an account

Re-running the relevant create step above is the reset. For a fully clean reset,
cancel any Stripe-test subscription, then re-subscribe / re-validate to return
the account to its target state. Update the `*_LICENCE_KEY` secret if a reset
mints a new key.

---

## 4. Bitbucket repository secrets — set + rotation

### Setting the secrets

Repository → **Settings → Repository variables**. For every secret in the matrix:

- Name: exactly as listed (case-sensitive).
- **Tick "Secured"** for all of them (they are credentials + keys).

Bitbucket injects repository variables into every Pipelines step as environment
variables, so the `E2E → Staging` step sees them with no extra wiring. The step's
guard fails fast and prints the exact missing variable names if any are unset.

### Rotation

- **Passwords:** change the account password on staging, then update the matching
  `*_PASSWORD` secret. Rotate on a fixed cadence and immediately on any suspected
  exposure.
- **Licence keys:** if a key is rotated/regenerated for an account, update the
  matching `*_LICENCE_KEY` secret in the same change — the rendered-key asserts
  will fail the moment the secret and the account's real key diverge.
- **Emails:** changing an account email requires updating `*_EMAIL` and confirming
  the account still resolves to its target state.

Never commit any of these values to the repo, the runbook, or pipeline YAML —
only the secret **names** are public; the values live solely in Bitbucket secured
variables.

---

## 5. Dirty-state recovery

The accounts must not be used by any other process during pipeline runs. If a run
fails because an account drifted, recover by **re-running the provisioning script**
(§2) for the affected state (`--state <STATE>`), which resets it idempotently.
The manual recovery table below is the fallback.

| Symptom | Likely cause | Recovery |
|---------|--------------|----------|
| Login times out / lands on `/login` | Wrong password, or password rotated without updating the secret | Verify login manually on staging; update `*_PASSWORD`; re-run |
| Active test shows lapsed / wrong tier | Subscription drifted (renewal failed, manual cancel) | Re-subscribe the account to the correct plan (Stripe test); confirm dashboard; re-run |
| Lapsed test shows active | Subscription was re-activated, or renewed | Cancel the Stripe-test subscription again so it lapses; confirm the Re-subscribe CTA; re-run |
| Rendered key ≠ expected (`cmd`/`clip` asserts fail) | `*_LICENCE_KEY` secret drifted from the account's real key | Read the account's real key on its page; update the secret to match; re-run |
| Free test shows not-activated | The free account was never validated, or its validation cache expired to never-validated | Validate the free account once (`revue activate <key>`); re-run |
| Signup-based test fails on email conflict | Orphan accounts accumulating from `logged_in_page` fresh signups | Periodically purge old `e2e-*@test.com` / `signup-*@test.com` staging accounts; these are created fresh per run by design and are safe to delete |
| Guard fails: "Missing required staging-E2E repository secret(s)" | Secret(s) not provisioned | Run the provisioning script (§2) or set the named secrets per §1/§4; re-run |

### Orphan accounts from fresh-signup tests

`test_auth_e2e.py`, `test_dashboard_e2e.py`, and the not-activated plan test sign
up brand-new users per run (`logged_in_page` / signup UI). Against staging this
accumulates throwaway accounts (`e2e-*@test.com`, `signup-*@test.com`,
`login-*@test.com`, etc.). They are harmless but should be purged periodically to
avoid email-conflict flakiness and keep the staging user table clean.

---

## 6. Logged gaps (AC7)

Per AC7, any state/assertion that cannot be reproduced on a static staging
account is logged here rather than hidden:

- **`test_active_pro_with_renewal_date`** (exact renewal date `2099-12-31`) is
  **skipped on staging**. The local SQL factory injects that far-future
  `current_period_end`; a real Stripe-test subscription cannot be forced to that
  literal, and staging has no DB to override it. Every other Active-Pro
  assertion still runs via the NULL-period variant
  (`test_active_pro_null_period_end`), so the only thing not exercised on staging
  is the literal-date rendering. No other state is silently skipped.

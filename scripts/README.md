# Revue Scripts

Internal tooling for the Revue development team.

---

## `generate_test_license.py`

Generates a `REVUE_LICENSE_KEY` for E2E testing and inserts it directly into
the production Fly.io database (`revue-io` → `/data/revue.db`).

**This script writes to production.** It is gated by a TOTP verification step
(1Password) or a passphrase stored in the macOS Keychain before it will connect
to Fly.io or print any key.

### Prerequisites

| Tool | Required for |
|------|-------------|
| `flyctl` | SSH into the Fly.io container |
| `op` (1Password CLI) | Preferred auth method |
| macOS Keychain | Fallback auth method |

Install flyctl: <https://fly.io/docs/hands-on/install-flyctl/>  
Install 1Password CLI: <https://developer.1password.com/docs/cli/get-started/>

---

### Setup — 1Password TOTP (preferred)

One-time setup per team member.

**Step 1 — Create the 1Password item**

In 1Password, create a new **Login** item named exactly:

```
Revue E2E License Generator
```

**Step 2 — Add a TOTP field**

Open the item → Edit → Add field → One-Time Password.

Generate a TOTP secret using the `op` CLI:

```bash
op item create \
  --category login \
  --title "Revue E2E License Generator" \
  --url "" \
  --generate-password=false
```

Then edit the item in the 1Password app and add a One-Time Password field with
any TOTP secret. Use a dedicated TOTP secret shared with the team (store the
raw secret in a separate Secure Note in the same vault so it can be re-enrolled
on a new device).

**Step 3 — Sign in before running**

```bash
op signin
```

`op` sessions expire; re-run `op signin` if the script reports an auth error.

**Step 4 — Optional: customise the item name**

If you use a different item name, set the environment variable:

```bash
export OP_ITEM="My Custom Item Name"
```

---

### Setup — macOS Keychain (fallback)

Use this on machines without the `op` CLI, or when 1Password is unavailable.

**Step 1 — Store a passphrase**

```bash
security add-generic-password \
  -a revue-license-generator \
  -s revue-license-generator \
  -w "your-strong-passphrase-here"
```

**Step 2 — Require confirmation on every access (important)**

1. Open **Keychain Access.app**
2. Find the `revue-license-generator` item (login keychain)
3. Right-click → **Get Info** → **Access Control** tab
4. Select **"Confirm before allowing access"**
5. Optionally enable **"Ask for Keychain password"** (triggers Touch ID / password dialog)

Without step 2, any process on your Mac can read the passphrase silently.

---

### Usage

```bash
# Authenticate and generate a key (auto-selects 1Password if op is available)
python3 scripts/generate_test_license.py --label github-e2e

# Explicitly use 1Password
python3 scripts/generate_test_license.py --label gitlab-e2e --auth 1password

# Explicitly use macOS Keychain
python3 scripts/generate_test_license.py --label bitbucket-e2e --auth keychain

# Verify auth works without writing to the DB
python3 scripts/generate_test_license.py --dry-run

# Use a different 1Password item name
OP_ITEM="Revue Staging Licenses" python3 scripts/generate_test_license.py
```

The `--label` value becomes the test user's email: `test-{label}@revue-test.local`.
Use a distinct label per platform so keys are traceable:

| Epic story | Suggested label |
|------------|----------------|
| REVUE-196 GitHub | `github-e2e` |
| REVUE-197 GitLab | `gitlab-e2e` |
| REVUE-198 Bitbucket | `bitbucket-e2e` |

All generated keys default to `tier=pro` (unlimited reviews) so E2E pipelines
do not fail on monthly usage limits.

---

### Revoking a key

If a test key is leaked or no longer needed, deactivate it directly in the
Fly.io database:

```bash
flyctl ssh console -a revue-io -C \
  "python3 -c \"import sqlite3; conn=sqlite3.connect('/data/revue.db'); conn.execute(\\\"UPDATE license_keys SET is_active=0 WHERE key=?\\\", ('lic_REPLACE_ME',)); conn.commit(); print('revoked')\""
```

Or SSH in interactively:

```bash
flyctl ssh console -a revue-io
# Inside the container:
python3
>>> import sqlite3
>>> conn = sqlite3.connect("/data/revue.db")
>>> conn.execute("UPDATE license_keys SET is_active=0 WHERE key=?", ("lic_REPLACE_ME",))
>>> conn.commit()
```

---

### Security notes

- **The 1Password gate proves possession** of the vault item. It requires an
  active `op` session (biometric or master password) to fetch the TOTP — if
  `op item get --otp` succeeds, authentication is confirmed. The current code
  is displayed for visual confirmation but not re-entered.
- **The Keychain gate** is weaker — it is a static passphrase gated by Touch ID
  on access. Prefer 1Password on machines where `op` is available.
- **`--dry-run` still requires auth.** The key is never printed without the gate
  passing, even in dry-run mode.
- **Keys are test-only.** Test emails (`@revue-test.local`) are recognisable in
  the DB. Do not use generated keys for real customer accounts.
- **Rotate after the epic closes.** Deactivate E2E keys once REVUE-195 is done
  — they are unlimited-review `pro` keys and should not remain active
  indefinitely.

# JWT signing key — operations runbook

The Revue licence-activation flow signs JWTs with an RSA-2048 private key
held as a Fly secret on the `revue-io` app. The CLI verifies signatures
locally against a public key embedded at Nuitka build time
(`revue_core.security.jwt_keys.JWT_PUBLIC_KEY_PEM`).

**Algorithm:** RS256 (RSA-2048 + SHA-256).

## Where the key lives

| Location | Half | Purpose |
|---|---|---|
| Fly secret `JWT_SIGNING_KEY` on app `revue-io` | private (base64-encoded PEM) | Signs JWTs in the `/api/v2/licence/activate` endpoint |
| 1Password vault `Private`, document `Revue JWT Signing Key (production)` | private (PEM, uploaded as a document) | Durable backup. The only authoritative record outside Fly |
| `packaging/revue_core/src/revue_core/security/jwt_keys.py` constant `JWT_PUBLIC_KEY_PEM` | public (PEM) | Embedded in every Nuitka-compiled CLI binary; verifies JWT signatures offline |

The public key is **safe to commit and ship**. The private key must
never exist on disk in this repo or in any shell session that is not
purpose-built for key handling.

## Generating a new keypair (initial setup or rotation)

> Treat this as a security-sensitive procedure. Run it from a fresh
> terminal with shell history disabled — see [Safety rules](#safety-rules) below.

```bash
HISTFILE=/dev/null zsh -l        # subshell with no history file
set -euo pipefail
umask 077                        # any file we create is 0600/0700

openssl genrsa -out /tmp/priv.pem 2048
openssl rsa -in /tmp/priv.pem -pubout -out /tmp/pub.pem

# 1. Back up the private key to 1Password BEFORE setting the Fly secret.
#    Use `op document create` so the PEM is uploaded as a file via the
#    filesystem path — the key never appears in argv (which would leak
#    it into `ps aux` for the duration of the call, contradicting the
#    "no private key in CLI args" safety rule below).
op document create /tmp/priv.pem \
  --title "Revue JWT Signing Key (production)" \
  --vault "Private"

# 2. Set the Fly secret as base64 (single-line, no PEM line-break handling)
PRIV_B64=$(base64 -i /tmp/priv.pem | tr -d '\n')
flyctl secrets set JWT_SIGNING_KEY="$PRIV_B64" -a revue-io
unset PRIV_B64

# 3. Overwrite + delete the disk PEM files (belt and braces against
#    APFS local snapshots — even though TRIM makes free-space recovery
#    impractical on SSD)
dd if=/dev/urandom of=/tmp/priv.pem bs=2048 count=1 conv=notrunc 2>/dev/null
rm -f /tmp/priv.pem

# 4. Print the public key — paste into jwt_keys.py constant + commit
cat /tmp/pub.pem
rm -f /tmp/pub.pem

exit
```

After this:

1. Edit `packaging/revue_core/src/revue_core/security/jwt_keys.py` —
   replace the `JWT_PUBLIC_KEY_PEM` constant with the new public key.
2. Re-run `pytest packaging/revue_core/tests/test_security_jwt_keys.py`
   to confirm the embedded key still parses.
3. Commit the change, open a PR, ship a new CLI release once merged.
4. Until the new CLI is released and customers upgrade, **both** the
   old and new private keys must be able to sign JWTs — see Rotation
   below.

## Backend usage

The backend reads the secret at boot and decodes:

```python
import base64, os
from revue_core.security.jwt_keys import JWT_SIGNING_KEY_ENV_VAR

priv_pem = base64.b64decode(os.environ[JWT_SIGNING_KEY_ENV_VAR])
# priv_pem is the RSA-2048 PEM, ready for PyJWT.encode(..., algorithm="RS256")
```

The base64 step is purely a transport convenience — Fly secrets are
stored opaquely, but accepting the PEM line breaks at the CLI argument
level is brittle, so we always pass base64 and decode on the backend.

## Rotation

A full rotation is **out of scope for REVUE-277**. The operational
shape below describes the staged procedure; the multi-key verifier is
itself a story-sized change (REVUE-278+) and is **not** implemented in
this codebase yet — `pyjwt.decode(token, key, …)` accepts a single key
string, not a tuple, so any rotation today requires a brief CLI-side
grace period rather than dual-key verification.

The procedure:

1. Generate a new keypair (procedure above) but **do not yet** swap
   the Fly secret.
2. Ship a CLI release whose `jwt_keys.py` exposes both the current
   public key (as `JWT_PUBLIC_KEY_PEM`) and the next one (as
   `JWT_PUBLIC_KEY_PEM_NEXT`). The verifier tries the current key
   first, falls back to `NEXT` only on `InvalidSignatureError`. (This
   is the verifier change — story-sized, deferred — that makes the
   rest of the rotation safe.)
3. After 100% of paying customers have the verifier-aware CLI release
   (track via usage telemetry once REVUE-278 lands), swap the Fly
   secret to the new private key. From this moment on, newly-signed
   JWTs verify under `JWT_PUBLIC_KEY_PEM_NEXT`.
4. Promote `_NEXT` to the primary constant in `jwt_keys.py`; the old
   key remains as a deprecated fallback. Ship another CLI release.
5. After grace period (recommended: 30 days, long enough for every
   issued JWT to expire and be re-signed under the new key), drop the
   deprecated old-key constant and ship a final CLI release.

The staging guarantees no customer loses access during the swap.
Earlier drafts of this runbook described `jwt_keys.py` holding a
"tuple of trusted keys" — that wording was misleading; PyJWT's
`decode` takes one key. The shape above (two named constants + a
verifier helper that tries each) is the real plan.

## Safety rules

- **Never** run `openssl genrsa` in an LLM session or any shell that
  records history without protection. Always use `HISTFILE=/dev/null zsh
  -l` to open a disposable subshell.
- **Never** print the private key in chat, commit messages, PR
  descriptions, or any log output. The public key is fine.
- **Never** pass the private key as a CLI argument to a long-running
  process — it would appear in `ps aux` for the duration. Use stdin or
  let `flyctl secrets set` substitute it via `"$VAR"` quoting from an
  in-shell variable that you `unset` immediately after.
- **Never** rely on `rm` alone to clear the disk file. Overwrite with
  random bytes via `dd if=/dev/urandom … conv=notrunc` first. APFS
  local snapshots may still capture pre-overwrite content for up to
  24h — accept that, or prune the snapshot manually with `tmutil
  deletelocalsnapshots`.

## Verifying the production secret matches the embedded public key

If you suspect drift between Fly and the embedded `JWT_PUBLIC_KEY_PEM`,
issue a probe JWT from the backend and verify it locally:

```bash
# On the Fly app
flyctl ssh console -a revue-io -C 'python -c "
import base64, os, jwt
priv = base64.b64decode(os.environ[\"JWT_SIGNING_KEY\"])
print(jwt.encode({\"probe\": True}, priv, algorithm=\"RS256\"))
"'

# Then locally, paste the token and verify
python -c '
import jwt
from revue_core.security.jwt_keys import JWT_PUBLIC_KEY_PEM
print(jwt.decode("<paste token>", JWT_PUBLIC_KEY_PEM, algorithms=["RS256"]))
'
```

If the second command raises `InvalidSignatureError`, the Fly secret
no longer matches the embedded public key — investigate before any
licence-activation traffic is served.

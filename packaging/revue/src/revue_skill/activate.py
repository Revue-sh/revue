"""``revue activate <key>`` — exchange a licence key for a signed JWT.

The CLI POSTs the user's licence key to the hardcoded activation
endpoint, verifies the returned RS256 JWT against the public key
embedded at Nuitka build time (``revue_core.security.jwt_keys``), and
writes the token to ``~/.config/revue/licence.jwt`` with file mode 0600
and parent-directory mode 0700.

Threat model:

- ``ACTIVATE_URL`` is a literal constant. **Never** read from an env
  var — a configurable URL is a licence-bypass vector (an attacker
  could redirect the request to a fake validator they control). See
  ``docs/runbooks/jwt-signing-key.md`` and the project memory
  ``project_license_validator_hardcoded``.
- Every JWT is verified before being written. A token that does not
  validate against the embedded public key never touches the disk.
- Network/server errors return non-zero exit codes with actionable
  messages — silent failures are explicitly forbidden by AC4.

Exit code table (referenced by CI automation):

    0  success
    2  network failure (transient — safe to retry)
    3  client error (4xx, e.g. invalid_key, inactive_licence — DON'T retry)
    4  unexpected response shape from server
    5  JWT verification failed (signature, claims, or expiry)
    6  server-side misconfiguration (5xx — safe to retry after operator fix)
    7  local file-system failure writing the licence
"""
from __future__ import annotations

import getpass
import hashlib
import os
import platform
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Final

import httpx
import jwt as pyjwt

# Import the module, not the constants, so the value is read at call time.
# A ``from … import JWT_PUBLIC_KEY_PEM`` would bind the constant locally at
# import time and resist monkeypatching in tests; module-attribute access
# always observes the current value.
from revue_core.security import jwt_keys as _jwt_keys


ACTIVATE_URL: Final[str] = "https://revue.sh/api/v2/licence/activate"
"""Production activation endpoint. Hardcoded — never read from an env
var. See module docstring for the threat-model rationale."""


_LICENCE_DIR_PERMS: Final[int] = 0o700
_LICENCE_FILE_PERMS: Final[int] = 0o600
_LICENCE_FILENAME: Final[str] = "licence.jwt"


def _build_http_client() -> httpx.Client:
    """Construct the httpx.Client used to POST to ``ACTIVATE_URL``.

    Factored into its own function so tests can patch it with a mock
    transport without touching the network. Real callers get a fresh
    client with a sane timeout.
    """
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))


def _safe_call(fn, *args, default: str = "") -> str:
    """Run ``fn(*args)``, returning ``default`` on KeyError/OSError.

    S8: containers and minimal Linux images can raise KeyError from
    ``getpass.getuser()`` (no entry in /etc/passwd) or OSError from
    ``platform.node()`` (uname unavailable). The fingerprint must
    degrade gracefully on these systems rather than crash activation.
    """
    try:
        return fn(*args) or default
    except (KeyError, OSError):
        return default


def _safe_mac_component() -> str:
    """Return the MAC component of the fingerprint, or '' if unavailable.

    S12: per the CPython docs, :func:`uuid.getnode` returns a randomised
    48-bit value with the multicast bit (1 << 40) set when no real MAC
    can be discovered. That randomness destroys fingerprint
    determinism, so when the multicast bit is set we omit the MAC
    component entirely — the rest of the fingerprint (hostname,
    username, OS, arch) stays deterministic.
    """
    try:
        node = uuid.getnode()
    except Exception:
        return ""
    # Multicast bit set ⇒ random fallback per Python docs.
    if node & (1 << 40):
        return ""
    return f"{node:x}"


def _machine_fingerprint() -> str:
    """Return an opaque, deterministic identifier for this machine.

    REVUE-277 encodes the fingerprint in the JWT claims for a future
    concurrent-machine cap (out of scope this story — claim is encoded
    but not enforced). The value is hashed so the raw inputs (username,
    hostname, MAC) are not visible to anyone inspecting the JWT.

    The components degrade gracefully (empty string fallback) when the
    platform info is unavailable — see S8 + S12 in the cycle-1 review.
    A deterministic fingerprint with a missing MAC is still useful;
    a crashed activate flow is not.
    """
    raw = "|".join(
        [
            _safe_call(platform.node),
            _safe_call(getpass.getuser),
            _safe_mac_component(),
            _safe_call(platform.system),
            _safe_call(platform.machine),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verify_jwt(token: str) -> dict:
    """Verify the JWT signature against the embedded public key. Returns
    the decoded claims on success; raises ``pyjwt.InvalidTokenError``
    (or a subclass) on any failure.

    ``options.require`` forces PyJWT to reject tokens missing any of the
    REVUE-277 mandatory claims. ``verify_exp`` is on by default but
    listed explicitly for clarity — see S1 in the cycle-1 review. The
    activate-time expiry check is defence-in-depth; the runtime expiry
    gate is REVUE-278's daily-check.
    """
    return pyjwt.decode(
        token,
        _jwt_keys.JWT_PUBLIC_KEY_PEM,
        algorithms=[_jwt_keys.JWT_ALGORITHM],
        options={
            "verify_exp": True,
            "require": ["exp", "workspace_id", "tier", "machine_fingerprint"],
        },
    )


def _write_licence_file(token: str) -> Path:
    """Write the JWT to ``~/.config/revue/licence.jwt`` with file mode
    0600 and parent directory mode 0700. Creates the parent directory
    if missing.

    The temp file is created via :func:`tempfile.mkstemp`, which opens
    with ``O_CREAT | O_EXCL | O_WRONLY`` and (on POSIX) mode 0600 —
    closing the TOCTOU window between ``write_text`` and a follow-up
    ``chmod`` that a naive implementation would expose (M1). The
    randomised suffix also means a stale tmp file from a crashed run
    can never collide and lock the path (D11).

    Returns the path of the written file.
    """
    # Honour REVUE_LICENCE_PATH override (mirrors emit_usage.py and the
    # REVUE_LICENCE_CACHE_PATH pattern in validate.py). Production paths
    # are unchanged when the env var is unset.
    env_override = os.environ.get("REVUE_LICENCE_PATH")
    if env_override:
        licence_file = Path(env_override)
        licence_dir = licence_file.parent
    else:
        licence_dir = Path.home() / ".config" / "revue"
        licence_file = licence_dir / _LICENCE_FILENAME
    licence_dir.mkdir(parents=True, exist_ok=True)
    # ``mkdir(mode=...)`` honours umask, so explicitly chmod the
    # directory after creation to guarantee 0700 regardless of umask.
    os.chmod(licence_dir, _LICENCE_DIR_PERMS)

    # mkstemp opens the fd with O_CREAT | O_EXCL | O_WRONLY at mode
    # 0600 on POSIX — no window in which the token exists at 0644.
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(licence_dir), prefix=".licence-", suffix=".tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(token)
        # Defence-in-depth: re-assert 0600 in case the platform's mkstemp
        # honoured a non-default umask (Windows in particular has no
        # POSIX perms; on POSIX this is a no-op).
        os.chmod(tmp_path, _LICENCE_FILE_PERMS)
        tmp_path.replace(licence_file)
    except BaseException:
        # Don't leave a stray tmp file on disk if anything explodes
        # between fd-open and replace.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return licence_file


def activate(key: str) -> int:
    """Run the activation flow. Returns the process exit code (0 on
    success, non-zero on any failure path documented in AC4).
    """
    payload = {"key": key, "machine_fingerprint": _machine_fingerprint()}

    try:
        with _build_http_client() as client:
            resp = client.post(ACTIVATE_URL, json=payload)
    except httpx.ConnectError as exc:
        print(
            f"error: could not reach the activation endpoint at "
            f"{ACTIVATE_URL} (network/connect failure: {exc}). "
            f"Check your network and try again.",
            file=sys.stderr,
        )
        return 2
    except httpx.HTTPError as exc:
        print(
            f"error: network failure talking to {ACTIVATE_URL}: {exc}",
            file=sys.stderr,
        )
        return 2

    if resp.status_code != 200:
        # Server returned an error envelope. Surface the documented
        # ``error`` code AND human ``message`` so the user can act.
        try:
            body = resp.json()
        except Exception:
            body = {}
        error_code = body.get("error", f"http_{resp.status_code}")
        message = body.get("message", f"server returned status {resp.status_code}")
        print(
            f"error: activation failed ({error_code}): {message}",
            file=sys.stderr,
        )
        # S6: split 4xx (don't retry) from 5xx (safe to retry once
        # operator fixes the misconfig) so CI automation can react
        # without parsing error_code strings.
        return 6 if 500 <= resp.status_code < 600 else 3

    try:
        body = resp.json()
        token = body["jwt"]
        envelope_tier = body.get("tier", "unknown")
    except Exception as exc:
        print(
            f"error: server returned an unexpected response shape: {exc}",
            file=sys.stderr,
        )
        return 4

    try:
        claims = _verify_jwt(token)
    except pyjwt.InvalidTokenError as exc:
        print(
            f"error: server returned a JWT whose signature could not be "
            f"verified against the embedded public key ({exc.__class__.__name__}: "
            f"{exc}). This should not happen in production — please report.",
            file=sys.stderr,
        )
        return 5
    except (pyjwt.InvalidKeyError, ValueError) as exc:
        # B3: ``pyjwt.decode`` calls into ``cryptography`` to parse the
        # embedded ``JWT_PUBLIC_KEY_PEM`` BEFORE the signature check
        # fires. A corrupted embedded PEM (build accident, botched key
        # rotation, copy-paste truncation) surfaces here:
        #
        # - PyJWT 2.x wraps the parse failure as ``InvalidKeyError`` —
        #   crucially NOT a subclass of ``InvalidTokenError``.
        # - Older / future variants may raise the underlying
        #   ``cryptography``-side ``ValueError`` directly.
        #
        # Either path produces an uncaught traceback under the
        # ``InvalidTokenError``-only branch above, violating AC4 ("no
        # silent / cryptic failures") and skipping the documented exit-5
        # path. Catch both, print an operator-actionable hint, exit 5.
        print(
            f"error: the CLI binary appears to have a corrupted embedded "
            f"public key — JWT verification could not start "
            f"({exc.__class__.__name__}: {exc}). Please report this to "
            f"support@revue.sh.",
            file=sys.stderr,
        )
        return 5

    # S2: trust the JWT, not the envelope. The envelope is convenient
    # for display but the JWT is the cryptographically verified source
    # of truth. A buggy or compromised backend that lies in the envelope
    # mustn't be able to silently change the user's tier.
    tier = claims.get("tier", "unknown")
    if envelope_tier != tier and envelope_tier != "unknown":
        print(
            f"warning: tier mismatch between JWT (verified: {tier!r}) and "
            f"HTTP envelope ({envelope_tier!r}). Trusting the JWT.",
            file=sys.stderr,
        )

    try:
        licence_file = _write_licence_file(token)
    except (PermissionError, OSError) as exc:
        # AC4 forbids silent failures. Read-only FS, locked-down parent
        # directory, exotic filesystem without POSIX perms — all surface
        # here. Tell the user which path and which OS error so they can
        # act (chmod the parent, mount writable, etc.).
        licence_dir = Path.home() / ".config" / "revue"
        print(
            f"error: could not write the licence file under {licence_dir} "
            f"({exc.__class__.__name__}: {exc}). Check that the directory is "
            f"writable by your user, or set $HOME to a writable location.",
            file=sys.stderr,
        )
        return 7
    print(f"activated: tier={tier}; licence written to {licence_file}")
    return 0

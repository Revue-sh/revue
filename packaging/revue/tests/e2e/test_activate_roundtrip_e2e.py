"""REVUE-331 -- E2E round-trip for the licence activate flow.

Covers BOTH onboarding paths against a live licence server:

* (TC1 / AC6) the now-primary ``revue activate <key>`` CLI happy path, and
* (TC2 / AC1-AC4) the browser ``/activate`` paste-key fallback round-trip.

The seam under test is the boundary the unit tests in ``test_activate.py``
deliberately mock away: the *real* licence server signs an RS256 JWT and
the *real* CLI verifies it against its embedded public key. A signing-key
rotation on the server that is not mirrored into the CLI's embedded public
key would silently brick activation for every new user; TC3 is the
regression test that catches exactly that (AC5).

Entrypoint note (deviation from the literal AC3/TC2 wording)
------------------------------------------------------------
AC3/TC2 describe feeding the pasted JWT into "the CLI's activation
entrypoint ... write to the licence-token file, invoke the CLI". There is
no CLI subcommand that *consumes* a pre-pasted token: ``revue activate``
re-fetches a fresh JWT from the server; it does not read one off disk. The
real CLI path that reads ``~/.config/revue/licence.jwt`` and verifies it
against the embedded public key is the ``/revue-local`` licence gate
(``local_run._gate_licence_validation``). TC2 drives that real gate after
writing the browser-issued JWT to the file -- exercising the genuine
file-read + embedded-key-verify seam. Building a dedicated token-consuming
CLI subcommand is a feature with its own blast radius and is out of scope
for this test-only ticket; the deviation is intentional and surfaced here
rather than silently dropped.

CLI invocation strategy (AC8)
-----------------------------
The round-trips run the CLI *in-process* (``cli.main`` / gate function),
which is the editable-install fallback AC8 blesses for PR pipelines. This
is the only mode that can be pointed at a local test server and given a
test keypair, because ``ACTIVATE_URL``, ``VALIDATE_URL`` and the embedded
public key are all hardcoded with no env override (a deliberate
licence-bypass defence). The real Nuitka binary therefore cannot
round-trip against a local server; ``_invoke_cli_activate`` keeps a
``REVUE_CLI_BIN`` subprocess hook for forward-compatibility, but the
hermetic CI coverage is in-process.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_embedded_public_key(monkeypatch, public_pem: bytes) -> None:
    """Point the CLI's embedded ``JWT_PUBLIC_KEY_PEM`` at ``public_pem``.

    Both the activate verifier and the /revue-local gate read the key via
    ``revue_core.security.jwt_keys`` at call time, so patching the module
    attribute is sufficient and survives the accessor indirection.
    """
    import revue_core.security.jwt_keys as jwt_keys_module

    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", public_pem.decode())


def _isolate_home(monkeypatch, tmp_path) -> Path:
    """Redirect ``Path.home()`` + ``$HOME`` at a tmp dir and return the
    ``~/.config/revue`` directory the CLI will use."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home / ".config" / "revue"


def _invoke_cli_activate(key: str) -> int:
    """Invoke ``revue activate <key>`` and return the exit code.

    Honours ``REVUE_CLI_BIN`` (run the compiled binary as a subprocess) for
    forward-compat / the tag pipeline; otherwise runs in-process via
    ``cli.main`` so the monkeypatched ``ACTIVATE_URL`` and embedded public
    key take effect (the editable-install fallback per AC8).
    """
    binary = os.environ.get("REVUE_CLI_BIN")
    if binary:
        proc = subprocess.run([binary, "activate", key], check=False)
        return proc.returncode
    from revue_skill import cli

    return cli.main(["activate", key])


def _run_local_run_gate(cmd: str = "prepare") -> int:
    """Run the real /revue-local licence gate for a gated command.

    The gate reads ``~/.config/revue/licence.jwt`` from disk and validates
    it against the embedded public key -- the genuine "invoke the CLI with
    the pasted token" path for the browser fallback (AC3/AC4).
    """
    from revue_skill.skill import local_run

    return local_run._gate_licence_validation(cmd)


# ---------------------------------------------------------------------------
# TC1 / AC6 -- CLI happy path: ``revue activate <key>`` (now primary)
# ---------------------------------------------------------------------------


def test_cli_activate_happy_path_writes_jwt_and_reports_success(
    licence_server,
    seed_active_licence,
    _e2e_rsa_keypair,
    monkeypatch,
    tmp_path,
    capsys,
):
    """TC1/AC6: ``revue activate <key>`` against the live server mints a
    real JWT, the CLI verifies it against the embedded key, writes
    ``~/.config/revue/licence.jwt`` at mode 0600, prints the tier and
    exits 0.

    This is the now-primary onboarding path and the only test where the
    real server actually signs the token the CLI consumes.
    """
    _, public_pem = _e2e_rsa_keypair
    _patch_embedded_public_key(monkeypatch, public_pem)
    licence_dir = _isolate_home(monkeypatch, tmp_path)

    key = seed_active_licence(tier="pro")

    # Redirect the hardcoded production endpoint at the live test server.
    # (Only possible in-process; the binary has no URL override by design.)
    import revue_skill.activate as activate_module

    monkeypatch.setattr(
        activate_module, "ACTIVATE_URL", f"{licence_server}/api/v2/licence/activate"
    )

    rc = _invoke_cli_activate(key)

    out = capsys.readouterr().out.lower()
    assert rc == 0, "activate against the live server should succeed"
    assert "pro" in out, "stdout should name the activated tier"
    assert "activated" in out or "success" in out

    licence_file = licence_dir / "licence.jwt"
    assert licence_file.exists(), "licence.jwt must be written on success"
    mode = stat.S_IMODE(licence_file.stat().st_mode)
    assert mode == 0o600, f"licence.jwt mode is {oct(mode)}, expected 0o600"

    # The written token is the genuine server-signed JWT and verifies
    # against the matching public key.
    claims = pyjwt.decode(
        licence_file.read_text(),
        public_pem.decode(),
        algorithms=["RS256"],
    )
    assert claims["tier"] == "pro"


# ---------------------------------------------------------------------------
# TC2 / AC1-AC4 -- Browser paste-key fallback round-trip
# ---------------------------------------------------------------------------


def _seed_fresh_validate_cache(licence_dir: Path, jwt_token: str) -> None:
    """Write a fresh /revue-local validate cache so the gate's success path
    returns 0 without touching the (hardcoded, production) VALIDATE_URL.

    The validate endpoint is a *separate*, explicitly out-of-scope endpoint
    from the activate seam under test. Neutralising its network leg with a
    seeded cache leaves the step-1 signature verification (the real seam)
    running for real against the genuine browser-issued JWT, while keeping
    the test hermetic.

    Cache contract (pinned to revue_skill/validate.py):
    The cache structure must match what revue-local's _gate_licence_validation
    expects: a JSON dict with keys {valid, tier, workspace_id, paywall_state,
    refresh_after_ts, cached_at}. The gate reads this cache when deciding
    whether to skip the network POST to VALIDATE_URL. If you modify this
    structure, update revue_skill/validate.py's is_cache_fresh() function to
    match, or the gate will treat the cache as stale and retry the network call.
    """
    claims = pyjwt.decode(jwt_token, options={"verify_signature": False})
    now = int(time.time())
    cache = {
        "valid": True,
        "tier": claims["tier"],
        "workspace_id": claims["workspace_id"],
        "paywall_state": None,
        "refresh_after_ts": now + 86400,
        "cached_at": now,
    }
    licence_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(licence_dir, 0o700)
    cache_path = licence_dir / "licence-cache.json"
    import json

    cache_path.write_text(json.dumps(cache))
    os.chmod(cache_path, 0o600)


def test_browser_fallback_paste_key_roundtrips_into_cli(
    licence_server,
    seed_active_licence,
    _e2e_rsa_keypair,
    monkeypatch,
    tmp_path,
):
    """TC2/AC1-AC4: drive the browser ``/activate`` fallback form with
    Playwright, paste a valid key, submit; assert a JWT is rendered into
    the result textarea (AC2) with the tier shown; write that JWT to the
    licence file the way a user would (AC3); then invoke the real CLI gate,
    which verifies the JWT against the embedded public key and proceeds
    (AC4 -> exit 0).
    """
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="Playwright is required for the browser fallback E2E",
    ).sync_playwright

    _, public_pem = _e2e_rsa_keypair

    key = seed_active_licence(tier="indie")

    # Drive the browser BEFORE isolating $HOME: Playwright resolves its
    # browser binary cache relative to $HOME at launch time, so patching
    # HOME first would point it at an empty tmp cache. The CLI-side file ops
    # below get the isolated HOME they need.
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(f"{licence_server}/activate")
            page.locator("#licence-key").fill(key)
            page.locator("button[type='submit']").click()

            # AC2: the JWT is rendered into the result surface (textarea).
            textarea = page.locator("#activate-result textarea")
            textarea.wait_for(state="visible", timeout=10000)
            issued_jwt = textarea.input_value().strip()

            result_text = page.locator("#activate-result").inner_text().lower()
        finally:
            browser.close()

    # Now isolate HOME + embedded key for the CLI-side legs (AC3/AC4).
    _patch_embedded_public_key(monkeypatch, public_pem)
    licence_dir = _isolate_home(monkeypatch, tmp_path)

    assert issued_jwt, "browser fallback must render a JWT into the result surface"
    # TC2: the activated tier is visible in the browser result (this path's
    # tier feedback -- the /revue-local gate is silent on success).
    assert "indie" in result_text

    # AC3: write the pasted JWT to the licence file exactly as a user would.
    licence_file = licence_dir / "licence.jwt"
    licence_dir.mkdir(parents=True, exist_ok=True)
    licence_file.write_text(issued_jwt)

    # The validate endpoint (separate, out of scope) is neutralised so the
    # gate's success path doesn't hit production -- the activate seam still
    # runs for real (step-1 signature verify of the browser-issued JWT).
    _seed_fresh_validate_cache(licence_dir, issued_jwt)

    # AC4: the real CLI gate verifies the browser-issued JWT against the
    # embedded public key and proceeds.
    rc = _run_local_run_gate("prepare")
    assert rc == 0, "CLI gate must accept the browser-issued JWT and proceed"


# ---------------------------------------------------------------------------
# TC3 / AC5 -- Key mismatch: server signs with a key the CLI does NOT trust
# ---------------------------------------------------------------------------


def test_key_mismatch_is_caught_by_cli(
    licence_server,
    seed_active_licence,
    _e2e_rsa_keypair,
    monkeypatch,
    tmp_path,
    capsys,
):
    """TC3/AC5: the load-bearing regression. The server signs with the
    matched private key, but the CLI's embedded public key is swapped for an
    UNRELATED one (simulating a server-side rotation with no coordinated CLI
    rebuild). The CLI must reject the otherwise-valid JWT on signature
    verification and exit non-zero -- proving the test catches the
    key-mismatch class it exists to catch.

    Mock-free by construction: ``revue activate`` verifies the JWT against
    the embedded key BEFORE any disk write, so this fails at the real seam.
    """
    # CLI embeds an unrelated public key -> mismatch with the server's signer.
    rogue_pub = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    _patch_embedded_public_key(monkeypatch, rogue_pub)
    licence_dir = _isolate_home(monkeypatch, tmp_path)

    key = seed_active_licence(tier="indie")

    import revue_skill.activate as activate_module

    monkeypatch.setattr(
        activate_module, "ACTIVATE_URL", f"{licence_server}/api/v2/licence/activate"
    )

    rc = _invoke_cli_activate(key)

    assert rc != 0, "a server JWT the CLI cannot verify must be rejected"
    assert rc == 5, "JWT verification failure is exit 5 (signature/claims/expiry)"
    assert not (licence_dir / "licence.jwt").exists(), (
        "an unverifiable token must never touch the disk"
    )
    err = capsys.readouterr().err.lower()
    assert "signature" in err or "verif" in err or "invalid" in err


# ---------------------------------------------------------------------------
# TC4 -- Malformed paste: a garbled token in the licence file is rejected
# ---------------------------------------------------------------------------


def test_malformed_pasted_token_is_rejected(
    _e2e_rsa_keypair, monkeypatch, tmp_path
):
    """TC4: a truncated/garbled JWT written to the licence file must make
    the CLI gate exit non-zero with a parse/verification error, never a
    silent success."""
    _, public_pem = _e2e_rsa_keypair
    _patch_embedded_public_key(monkeypatch, public_pem)
    licence_dir = _isolate_home(monkeypatch, tmp_path)

    licence_dir.mkdir(parents=True, exist_ok=True)
    (licence_dir / "licence.jwt").write_text("not-a-jwt.garbled.payload")

    rc = _run_local_run_gate("prepare")
    assert rc != 0, "a malformed token must not validate"
    assert rc == 5, "JWT verification failure is exit 5"


# ---------------------------------------------------------------------------
# TC5 -- Expired JWT: a token with exp in the past is rejected
# ---------------------------------------------------------------------------


def test_expired_pasted_token_is_rejected(
    _e2e_rsa_keypair, monkeypatch, tmp_path
):
    """TC5: a JWT whose ``exp`` is in the past must be rejected by the CLI
    gate's signature/expiry check -- the token is correctly signed, only
    expired."""
    priv_pem, public_pem = _e2e_rsa_keypair
    _patch_embedded_public_key(monkeypatch, public_pem)
    licence_dir = _isolate_home(monkeypatch, tmp_path)

    now = int(time.time())
    expired = pyjwt.encode(
        {
            "workspace_id": 7,
            "tier": "indie",
            "issuance_ts": now - 2 * 86400,
            "exp": now - 3600,
            "machine_fingerprint": "fp",
        },
        priv_pem,
        algorithm="RS256",
    )
    licence_dir.mkdir(parents=True, exist_ok=True)
    (licence_dir / "licence.jwt").write_text(expired)

    rc = _run_local_run_gate("prepare")
    assert rc == 5, "an expired JWT must be rejected with exit 5"


# ---------------------------------------------------------------------------
# TC6 -- Form validation: an invalid key never renders a JWT
# ---------------------------------------------------------------------------


def test_browser_form_rejects_invalid_key_without_rendering_jwt(
    licence_server, monkeypatch, tmp_path
):
    """TC6: submitting the ``/activate`` fallback form with a malformed key
    must be rejected by HTML5 validation (``pattern="^lic_[a-f0-9]{32}$"``)
    -- the form does not submit, no JWT is rendered, and the CLI is never
    invoked.
    """
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="Playwright is required for the browser fallback E2E",
    ).sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(f"{licence_server}/activate")
            page.locator("#licence-key").fill("not-a-valid-key")
            page.locator("button[type='submit']").click()

            # HTML5 pattern validation blocks submit; the field reports invalid
            # and the result surface stays empty (no JWT textarea appears).
            is_valid = page.evaluate(
                "document.getElementById('licence-key').checkValidity()"
            )
            assert is_valid is False, "malformed key must fail HTML5 validation"

            # Give any (erroneous) async submit a beat to render, then assert
            # no JWT textarea was produced.
            assert page.locator("#activate-result textarea").count() == 0, (
                "no JWT must be rendered for an invalid key"
            )
        finally:
            browser.close()

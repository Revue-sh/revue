"""REVUE-277 Phase 3 — ``revue activate <key>`` CLI subcommand.

Covers AC2 (file written with 0600 perms), AC3 (headless activate
succeeds), AC4 (actionable error messages for invalid_key /
inactive_licence / network failure), AC5 (JWT signature verification
against embedded public key, tampered tokens rejected).

The HTTP layer is mocked via ``httpx.MockTransport`` so tests don't
require a live backend.
"""
from __future__ import annotations

import base64
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# ---------------------------------------------------------------------------
# Session-scoped test keypair — generated once per session, never the
# production key. Tests patch the embedded ``JWT_PUBLIC_KEY_PEM`` constant
# so verification round-trips against a key the test owns.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _test_rsa_keypair() -> tuple[bytes, bytes]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


@pytest.fixture
def _patched_public_key(monkeypatch, _test_rsa_keypair):
    """Replace the embedded production public key with the test public key."""
    _, pub_pem = _test_rsa_keypair
    import revue_core.security.jwt_keys as jwt_keys_module
    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", pub_pem.decode())
    return pub_pem


def _sign_test_jwt(
    priv_pem: bytes,
    *,
    workspace_id: int = 7,
    tier: str = "indie",
    machine_fingerprint: str = "abc-123",
    expiry_days: int = 365,
) -> str:
    """Sign a JWT with the test private key, mirroring the backend."""
    now = datetime.now(timezone.utc)
    return pyjwt.encode(
        {
            "workspace_id": workspace_id,
            "tier": tier,
            "issuance_ts": int(now.timestamp()),
            # Standard PyJWT claim name; the verifier honours `exp` for
            # expiry enforcement. See S1 / jwt_signing.py.
            "exp": int((now + timedelta(days=expiry_days)).timestamp()),
            "machine_fingerprint": machine_fingerprint,
        },
        priv_pem,
        algorithm="RS256",
    )


# ---------------------------------------------------------------------------
# HTTP transport factory — installs a deterministic mock so tests don't
# hit the live backend.
# ---------------------------------------------------------------------------


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _success_handler(jwt_token: str, tier: str = "indie"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jwt": jwt_token, "tier": tier})
    return handler


def _error_handler(status: int, body: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return handler


def _network_error_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")
    return handler


# ---------------------------------------------------------------------------
# Isolate the licence file location per test
# ---------------------------------------------------------------------------


@pytest.fixture
def _licence_dir(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home / ".config" / "revue"


# ---------------------------------------------------------------------------
# AC3 / TC1 / TC2 — Headless activate happy path
# ---------------------------------------------------------------------------


def test_activate_writes_jwt_and_returns_zero_on_success(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """AC3 / TC2: ``revue activate KEY`` succeeds → JWT written, exit 0."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token))),
    ):
        rc = activate_module.activate("lic_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    assert rc == 0
    licence_file = _licence_dir / "licence.jwt"
    assert licence_file.exists()
    assert licence_file.read_text() == token


def test_activate_prints_tier_on_success(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """Operator feedback: stdout names the tier so the user can confirm."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem, tier="pro")

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token, tier="pro"))),
    ):
        rc = activate_module.activate("lic_key")

    out = capsys.readouterr().out.lower()
    assert rc == 0
    assert "pro" in out
    assert "activated" in out or "success" in out


# ---------------------------------------------------------------------------
# AC2 / TC5 — JWT file permissions are 0600, parent dir 0700
# ---------------------------------------------------------------------------


def test_activate_writes_jwt_with_owner_only_perms(
    _patched_public_key, _licence_dir, _test_rsa_keypair
):
    """AC2 / TC5: licence.jwt is mode 0600, parent dir is 0700."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token))),
    ):
        activate_module.activate("lic_key")

    licence_file = _licence_dir / "licence.jwt"
    file_mode = stat.S_IMODE(licence_file.stat().st_mode)
    dir_mode = stat.S_IMODE(_licence_dir.stat().st_mode)
    assert file_mode == 0o600, f"licence.jwt mode is {oct(file_mode)}, expected 0o600"
    assert dir_mode == 0o700, f"~/.config/revue mode is {oct(dir_mode)}, expected 0o700"


# ---------------------------------------------------------------------------
# AC4 / TC3 — Invalid key error
# ---------------------------------------------------------------------------


def test_activate_rejects_invalid_key(
    _patched_public_key, _licence_dir, capsys
):
    """AC4 / TC3: server returns 404 invalid_key → exit non-zero,
    actionable message, no file written."""
    from revue_skill import activate as activate_module

    handler = _error_handler(
        404,
        {
            "error": "invalid_key",
            "message": "Licence key not recognised. Double-check the key.",
        },
    )
    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(handler)),
    ):
        rc = activate_module.activate("lic_bogus")

    assert rc != 0
    assert not (_licence_dir / "licence.jwt").exists()
    err = capsys.readouterr().err.lower()
    assert "not recognised" in err or "not recognized" in err or "invalid" in err


# ---------------------------------------------------------------------------
# S6 — 5xx server errors get a distinct exit code (6) so CI can retry
# ---------------------------------------------------------------------------


def test_activate_returns_exit_6_on_server_5xx(
    _patched_public_key, _licence_dir, capsys
):
    """S6: a 500-class response (e.g. server_misconfigured) is operator
    error on the server side. CI automation must be able to retry these
    without conflating them with permanent 4xx failures, so route 5xx
    to a distinct exit code (6)."""
    from revue_skill import activate as activate_module

    handler = _error_handler(
        500,
        {
            "error": "server_misconfigured",
            "message": "JWT_SIGNING_KEY is unset; see runbook.",
        },
    )
    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(handler)),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 6
    assert not (_licence_dir / "licence.jwt").exists()


def test_activate_returns_exit_3_on_4xx(_patched_public_key, _licence_dir):
    """S6 cross-check: 4xx stays at exit 3 (no retry warranted)."""
    from revue_skill import activate as activate_module

    handler = _error_handler(404, {"error": "invalid_key", "message": "nope"})
    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(handler)),
    ):
        rc = activate_module.activate("lic_key")
    assert rc == 3


# ---------------------------------------------------------------------------
# S8 — _machine_fingerprint must handle KeyError/OSError from getpass/platform
# ---------------------------------------------------------------------------


def test_machine_fingerprint_survives_getuser_keyerror(monkeypatch):
    """S8: containers without a resolvable UID can raise KeyError from
    getpass.getuser(). The fingerprint must degrade gracefully (use the
    empty string) rather than crash the activate flow."""
    import getpass as gp
    from revue_skill import activate as activate_module

    def _raise(*args, **kwargs):
        raise KeyError("getpwuid(): uid not found: 65535")

    monkeypatch.setattr(gp, "getuser", _raise)
    # Should not raise
    fp = activate_module._machine_fingerprint()
    assert isinstance(fp, str) and len(fp) == 64  # sha256 hex


def test_machine_fingerprint_survives_platform_node_oserror(monkeypatch):
    """S8: platform.node() can raise OSError on exotic systems."""
    import platform as pl
    from revue_skill import activate as activate_module

    def _raise():
        raise OSError("uname unavailable")

    monkeypatch.setattr(pl, "node", _raise)
    fp = activate_module._machine_fingerprint()
    assert isinstance(fp, str) and len(fp) == 64


# ---------------------------------------------------------------------------
# S12 — uuid.getnode() random fallback (multicast bit set) → omit MAC
# ---------------------------------------------------------------------------


def test_machine_fingerprint_is_deterministic_with_random_getnode(monkeypatch):
    """S12: per CPython docs, uuid.getnode() returns a randomised 48-bit
    value with the multicast bit set when no MAC can be discovered. That
    randomness leaks into the fingerprint and breaks determinism, which
    breaks any future concurrent-machine cap. Detect the multicast bit
    and omit the MAC component; the fingerprint must then be stable
    across repeated calls within the same process."""
    import uuid as _uuid
    from revue_skill import activate as activate_module

    # Set the multicast bit (1 << 40); CPython uses this exact pattern
    # to signal a random fallback.
    fake_node = (1 << 40) | 0xABCDEF
    monkeypatch.setattr(_uuid, "getnode", lambda: fake_node)
    fp_a = activate_module._machine_fingerprint()

    # Different random fallback value → fingerprint must still be the
    # same (proving the MAC component was omitted, not just hashed).
    monkeypatch.setattr(_uuid, "getnode", lambda: (1 << 40) | 0x123456)
    fp_b = activate_module._machine_fingerprint()

    assert fp_a == fp_b, (
        "fingerprint changed across two random getnode() calls — "
        "the multicast-bit random fallback is leaking into the hash"
    )


# ---------------------------------------------------------------------------
# AC4 / TC4 — Inactive licence (proxy for "exhausted seat")
# ---------------------------------------------------------------------------


def test_activate_rejects_inactive_licence(
    _patched_public_key, _licence_dir, capsys
):
    """AC4 / TC4: server returns 403 inactive_licence → exit non-zero,
    actionable message that names the remediation channel."""
    from revue_skill import activate as activate_module

    handler = _error_handler(
        403,
        {
            "error": "inactive_licence",
            "message": "This licence is no longer active. Contact support@revue.sh to reactivate.",
        },
    )
    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(handler)),
    ):
        rc = activate_module.activate("lic_inactive")

    assert rc != 0
    assert not (_licence_dir / "licence.jwt").exists()
    err = capsys.readouterr().err
    assert "support@revue.sh" in err or "support" in err.lower()


# ---------------------------------------------------------------------------
# AC4 — Network failure
# ---------------------------------------------------------------------------


def test_activate_handles_network_failure(
    _patched_public_key, _licence_dir, capsys
):
    """AC4: network unreachable → exit non-zero, actionable hint, no file."""
    from revue_skill import activate as activate_module

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_network_error_handler())),
    ):
        rc = activate_module.activate("lic_key")

    assert rc != 0
    assert not (_licence_dir / "licence.jwt").exists()
    err = capsys.readouterr().err.lower()
    assert "network" in err or "connect" in err or "unreachable" in err


# ---------------------------------------------------------------------------
# AC5 / TC6 — JWT signature verification (wrong key → reject)
# ---------------------------------------------------------------------------


def test_activate_rejects_jwt_signed_by_wrong_key(
    _patched_public_key, _licence_dir, capsys
):
    """AC5 / TC6: a JWT signed by a key OTHER than the embedded one is
    rejected before being written. Defends against a compromised backend
    issuing forged tokens against a different signing identity."""
    from revue_skill import activate as activate_module

    # Sign with a fresh keypair that the embedded constant does NOT trust
    rogue_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rogue_priv_pem = rogue_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    rogue_token = _sign_test_jwt(rogue_priv_pem)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(rogue_token))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc != 0
    assert not (_licence_dir / "licence.jwt").exists()
    err = capsys.readouterr().err.lower()
    assert "signature" in err or "verif" in err or "invalid" in err


def test_activate_rejects_tampered_jwt(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """AC5: a JWT whose payload has been tampered with after signing is
    rejected. Tamper the middle base64 chunk so the signature no longer
    matches."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    valid = _sign_test_jwt(priv_pem)
    # JWT = header.payload.signature ; flip a character in payload
    header, payload, sig = valid.split(".")
    tampered_payload = payload[:-2] + ("AA" if not payload.endswith("AA") else "BB")
    tampered = f"{header}.{tampered_payload}.{sig}"

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(tampered))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc != 0
    assert not (_licence_dir / "licence.jwt").exists()


# ---------------------------------------------------------------------------
# Threat-model rule: activate URL is hardcoded (project_license_validator_hardcoded)
# ---------------------------------------------------------------------------


def test_activate_url_is_hardcoded_not_env_overridable(
    _patched_public_key, _licence_dir, _test_rsa_keypair, monkeypatch
):
    """S4 / threat-model rule: the activate endpoint URL must be baked
    into the binary. An env override (e.g. ``REVUE_ACTIVATE_URL=evil``)
    would be a license-bypass vector.

    Earlier implementations of this test scanned the module source for
    ``os.getenv`` near the ``ACTIVATE_URL`` line, which a ``_resolve_url``
    helper could trivially evade. This version sets a plausible env-var
    override BEFORE the module is (re-)imported AND mounts a mock
    transport that pins the URL to the expected literal — the assertion
    runs in the transport, so any indirect override anywhere in the
    call chain is surfaced regardless of whether it lands in the
    ACTIVATE_URL constant or in a per-call ``_resolve_url`` helper.
    """
    import importlib
    import revue_skill.activate as activate_module

    # Set every plausible env-var name that a future refactor might
    # accidentally honour. Set BEFORE the reload so a module-level
    # ``os.environ.get`` would observe the override.
    for name in (
        "REVUE_ACTIVATE_URL",
        "ACTIVATE_URL",
        "REVUE_API_URL",
        "REVUE_LICENCE_URL",
    ):
        monkeypatch.setenv(name, "http://evil.example.invalid/fake")

    # Force a fresh import so module-level env-reads (the most common
    # regression shape) are observed.
    activate_module = importlib.reload(activate_module)
    # The reload re-binds the JWT_PUBLIC_KEY_PEM lookup back to the
    # production key, so re-patch the public key for the verify call.
    import revue_core.security.jwt_keys as jwt_keys_module
    _, pub_pem = _test_rsa_keypair
    monkeypatch.setattr(jwt_keys_module, "JWT_PUBLIC_KEY_PEM", pub_pem.decode())

    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    requested_urls: list[str] = []

    # Pin to the literal expected URL, NOT to activate_module.ACTIVATE_URL —
    # a regression that read the env var into ACTIVATE_URL itself would
    # otherwise pass an `== ACTIVATE_URL` check.
    EXPECTED_URL = "https://revue.sh/api/v2/licence/activate"

    def _pinning_handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={"jwt": token, "tier": "indie"})

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_pinning_handler)),
    ):
        activate_module.activate("lic_key")

    # The single recorded URL must be the production literal; if any
    # env-var leaked through (into ACTIVATE_URL or via a per-call
    # helper), this list will contain "http://evil.example.invalid/fake".
    assert requested_urls == [EXPECTED_URL], (
        f"activate POSTed to {requested_urls} — env override leaked through"
    )
    assert activate_module.ACTIVATE_URL == EXPECTED_URL


# ---------------------------------------------------------------------------
# S3 — `alg=none` defence: a forged unsigned token must be rejected
# ---------------------------------------------------------------------------


def test_activate_rejects_alg_none_jwt(
    _patched_public_key, _licence_dir, capsys
):
    """S3: a JWT with ``{"alg": "none"}`` and no signature must be
    rejected. ``algorithms=["RS256"]`` is pinned in code; this test
    guards against a future refactor that silently widens the list."""
    from revue_skill import activate as activate_module

    # Build a token manually: base64url(header) . base64url(payload) . ""
    def b64u(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    header = {"alg": "none", "typ": "JWT"}
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "workspace_id": 7,
        "tier": "pro",  # attacker tries to upgrade themselves
        "issuance_ts": now,
        "exp": now + 86400,
        "machine_fingerprint": "fp",
    }
    forged = f"{b64u(header)}.{b64u(payload)}."  # trailing empty signature

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(forged))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 5
    assert not (_licence_dir / "licence.jwt").exists()


# ---------------------------------------------------------------------------
# CLI plumbing — `revue activate <key>` is wired through main()
# ---------------------------------------------------------------------------


def test_cli_activate_subcommand_wired_to_main(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """The argparse plumbing must route ``revue activate <key>`` to the
    activate function. Verifies the subcommand exists and the main
    entry point's exit code matches."""
    from revue_skill import cli, activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token))),
    ):
        rc = cli.main(["activate", "lic_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"])

    assert rc == 0
    assert (_licence_dir / "licence.jwt").exists()


# ---------------------------------------------------------------------------
# M1 — TOCTOU: file must NEVER exist on disk with permissions > 0600, not
# even transiently between create-and-chmod. Even if umask is 0o022 (so a
# default file would be 0o644), the licence.jwt must be 0o600 from the
# moment of creation.
# ---------------------------------------------------------------------------


def test_activate_writes_licence_file_atomically_at_0600(
    _patched_public_key, _licence_dir, _test_rsa_keypair, monkeypatch
):
    """M1 regression: the JWT file must be created at mode 0600 atomically.

    A naive ``Path.write_text`` honours the process umask, which leaves the
    file world-readable (0644) for the window between write and the
    follow-up ``chmod`` to 0600. We close that window by intercepting
    ``os.chmod`` and asserting that, if it is called against the licence
    file at all, the file is already at 0600 (i.e. there was no widening
    window).
    """
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    # Deliberately set a permissive umask so the bug, if present, surfaces
    # as a 0644 (or similar) created-state.
    monkeypatch.setattr(os, "umask", lambda mask: 0o022)
    os.umask(0o022)

    captured_modes: list[int] = []
    real_chmod = os.chmod

    def _spy_chmod(path, mode, *args, **kwargs):
        # If the file already exists, capture its current mode before chmod.
        try:
            st = os.stat(path)
            captured_modes.append((str(path), stat.S_IMODE(st.st_mode), mode))
        except FileNotFoundError:
            pass
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", _spy_chmod)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 0
    # Find any chmod call against the JWT file (or its tmp shadow). The
    # current code chmods the .tmp file AFTER write_text creates it under
    # umask — captured mode would be 0o644. The fix creates the file via
    # os.open(..., 0o600) so the captured mode at chmod time (if chmod is
    # still called for belt-and-braces) is already 0o600.
    jwt_related = [m for m in captured_modes if "licence" in m[0] or m[0].endswith(".jwt") or ".tmp" in m[0]]
    for path, pre_mode, target_mode in jwt_related:
        # If this is a file chmod (not the parent dir to 0o700), the
        # pre-existing mode must already be 0o600 — the file was created
        # with the right mode from the start.
        if target_mode == 0o600:
            assert pre_mode == 0o600, (
                f"licence file at {path} was created with mode {oct(pre_mode)} "
                f"and only chmod'd to 0600 afterwards — TOCTOU race window present"
            )


# ---------------------------------------------------------------------------
# S7 — OSError / PermissionError on file ops → actionable stderr, exit 7
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S1 — JWT expiry enforcement: an expired token must be rejected, no file
# ---------------------------------------------------------------------------


def test_activate_rejects_expired_jwt(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """S1: a JWT signed with `exp` in the past must be rejected by the
    verifier (PyJWT honours `exp` automatically). CLI exits 5, no file
    written. Defence-in-depth at write time even though the daily-check
    (REVUE-278) is the runtime expiry gate."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair

    # Sign a JWT whose `exp` is 1 hour in the past.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = pyjwt.encode(
        {
            "workspace_id": 7,
            "tier": "indie",
            "issuance_ts": int((past - timedelta(days=1)).timestamp()),
            "exp": int(past.timestamp()),
            "machine_fingerprint": "fp",
        },
        priv_pem,
        algorithm="RS256",
    )

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(expired))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 5
    assert not (_licence_dir / "licence.jwt").exists()


def test_activate_rejects_jwt_missing_required_claims(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """S1: the verifier must explicitly require `exp`, `workspace_id`,
    `tier`, and `machine_fingerprint`. A JWT missing any of these (e.g.
    a probe token from the runbook) must be rejected."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair

    # Token with valid signature but no required claims
    naked = pyjwt.encode({"probe": True}, priv_pem, algorithm="RS256")

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(naked))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 5
    assert not (_licence_dir / "licence.jwt").exists()


# ---------------------------------------------------------------------------
# S2 — verified claims source: tier must come from the JWT, not the envelope
# ---------------------------------------------------------------------------


def test_activate_uses_tier_from_verified_jwt_not_envelope(
    _patched_public_key, _licence_dir, _test_rsa_keypair, capsys
):
    """S2: when the HTTP envelope's `tier` disagrees with the JWT's
    verified `tier` claim, the CLI must trust the JWT and warn about
    the mismatch. A compromised or buggy backend mustn't be able to
    upgrade the user's tier just by lying in the JSON envelope."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair

    # JWT says indie; envelope claims pro
    token = _sign_test_jwt(priv_pem, tier="indie")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jwt": token, "tier": "pro"})

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(handler)),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out.lower()
    err = captured.err.lower()
    # The user-facing tier is the verified one
    assert "indie" in out
    assert "pro" not in out
    # The mismatch must be surfaced (stderr warning)
    assert "mismatch" in err or "warning" in err


def test_activate_returns_exit_5_on_corrupt_embedded_public_key(
    _licence_dir, _test_rsa_keypair, monkeypatch, capsys
):
    """B3: a corrupted embedded ``JWT_PUBLIC_KEY_PEM`` (build accident,
    botched key rotation) raises ``InvalidKeyError`` from inside
    ``pyjwt.decode`` BEFORE the signature check fires. That class is NOT
    a subclass of ``InvalidTokenError``, so an ``except InvalidTokenError``
    branch alone would leak an uncaught traceback to the user — violating
    AC4 ("no silent / cryptic failures") and skipping the documented
    exit-5 path. The widened except must catch the corrupt-key case and
    exit 5 like every other verify-side failure, with an
    operator-actionable stderr message and no file on disk.
    """
    from revue_skill import activate as activate_module
    import revue_core.security.jwt_keys as jwt_keys_module

    # Replace the embedded key with a syntactically PEM-shaped but
    # cryptographically nonsense blob. ``cryptography`` cannot parse this
    # as an RSA SubjectPublicKeyInfo and PyJWT wraps the failure as
    # ``InvalidKeyError``.
    monkeypatch.setattr(
        jwt_keys_module,
        "JWT_PUBLIC_KEY_PEM",
        "-----BEGIN PUBLIC KEY-----\nNOT_A_REAL_KEY\n-----END PUBLIC KEY-----\n",
    )

    # Sign a JWT with a real test key — the token itself is well-formed;
    # the failure is on the verifier side (corrupted embedded key).
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 5
    assert not (_licence_dir / "licence.jwt").exists()
    err = capsys.readouterr().err.lower()
    # Operator-actionable hint: names the corrupted-key cause and the
    # remediation channel; no raw traceback leakage.
    assert "corrupt" in err or "embedded" in err
    assert "support@revue.sh" in err or "report" in err
    assert "traceback" not in err


def test_activate_returns_exit_7_on_permission_error(
    _patched_public_key, _licence_dir, _test_rsa_keypair, monkeypatch, capsys
):
    """S7: if the licence dir cannot be created (read-only FS, perms), the
    CLI must surface an actionable stderr and exit 7 — never a raw traceback."""
    from revue_skill import activate as activate_module
    priv_pem, _ = _test_rsa_keypair
    token = _sign_test_jwt(priv_pem)

    real_mkdir = Path.mkdir

    def _denying_mkdir(self, *args, **kwargs):
        if ".config/revue" in str(self) or str(self).endswith("/revue"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _denying_mkdir)

    with patch.object(
        activate_module, "_build_http_client",
        return_value=httpx.Client(transport=_mock_transport(_success_handler(token))),
    ):
        rc = activate_module.activate("lic_key")

    assert rc == 7
    err = capsys.readouterr().err
    # Actionable: mentions the directory and the OS-level reason
    assert "revue" in err.lower()
    assert "permission" in err.lower() or "denied" in err.lower()

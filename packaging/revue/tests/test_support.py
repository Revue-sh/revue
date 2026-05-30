"""REVUE-359 — support-contact surfaced in every CLI error path + docs.

A user who hits an activation, install, or licence-validation failure must be
given one actionable next step ("Need help? Email support@revue.sh") so a
failed first run lands somewhere actionable.

Covers:
- AC1: activation / install / licence-validation errors include the exact
  support line.
- AC2: README has a Support section with the email + "issues coming soon" note.
- AC3: the /revue-local SKILL.md troubleshooting section includes the note.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# The exact copy the ACs pin. Any drift in wording is a regression.
EXPECTED_SUPPORT_LINE = "Need help? Email support@revue.sh"


# ---------- support module --------------------------------------------------

def test_support_line_uses_exact_ac_copy():
    # Arrange / Act
    from revue_skill.support import SUPPORT_LINE

    # Assert — pinned to the AC string verbatim
    assert SUPPORT_LINE == EXPECTED_SUPPORT_LINE


def test_support_email_constant_is_canonical_address():
    # Arrange / Act
    from revue_skill.support import SUPPORT_EMAIL

    # Assert
    assert SUPPORT_EMAIL == "support@revue.sh"


def test_support_footer_is_pure_and_returns_the_ac_line(capsys):
    # Arrange
    from revue_skill.support import support_footer

    # Act
    result = support_footer()

    # Assert — pure: returns the AC string, performs NO I/O. The imperative
    # shell (CLI callers) owns the stderr write — see REVUE-359 review #573
    # (SRP fix: support module owns policy, not output mechanism).
    captured = capsys.readouterr()
    assert result == EXPECTED_SUPPORT_LINE
    assert captured.out == ""
    assert captured.err == ""


# ---------- activation failure (revue activate) -----------------------------

def test_cli_emits_support_footer_when_activation_fails(monkeypatch, capsys):
    # Arrange — network is unreachable, so activate() returns a non-zero code
    from revue_skill import cli

    class _ConnectErrorClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("no route to host")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "revue_skill.activate._build_http_client", lambda: _ConnectErrorClient()
    )

    # Act
    rc = cli.main(["activate", "lic_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"])

    # Assert — failed activation surfaces the support line
    assert rc != 0
    assert EXPECTED_SUPPORT_LINE in capsys.readouterr().err


# ---------- install failure (revue install-skill) ---------------------------

def test_cli_emits_support_footer_when_install_fails(monkeypatch, tmp_path, capsys):
    # Arrange — manifest fetch fails, so install-skill returns a non-zero code
    from revue_skill import cli

    def _boom(_url):
        raise httpx.ConnectError("manifest host unreachable")

    monkeypatch.setattr("revue_skill.cli._fetch_manifest", _boom)

    # Act
    rc = cli.main(["install-skill", "--target-dir", str(tmp_path)])

    # Assert — failed install surfaces the support line
    assert rc != 0
    assert EXPECTED_SUPPORT_LINE in capsys.readouterr().err


def test_cli_emits_support_footer_when_install_raises_uncaught(
    monkeypatch, tmp_path, capsys
):
    # Arrange — manifest verify is skipped, then install() blows up mid-copy
    # (e.g. read-only skills dir). The boundary must catch it, not crash.
    from revue_skill import cli

    def _explode(*_args, **_kwargs):
        raise PermissionError("skills dir is read-only")

    monkeypatch.setattr("revue_skill.cli.install", _explode)

    # Act
    rc = cli.main(["install-skill", "--skip-verify", "--target-dir", str(tmp_path)])

    # Assert — uncaught install failure surfaces the support line, no traceback
    assert rc != 0
    assert EXPECTED_SUPPORT_LINE in capsys.readouterr().err


# ---------- success path must stay quiet ------------------------------------

def test_cli_does_not_emit_support_footer_on_success(capsys):
    # Arrange / Act — `version` always succeeds and touches no network
    from revue_skill import cli

    rc = cli.main(["version"])

    # Assert — no support noise on the happy path
    captured = capsys.readouterr()
    assert rc == 0
    assert EXPECTED_SUPPORT_LINE not in captured.out
    assert EXPECTED_SUPPORT_LINE not in captured.err


# ---------- licence-validation failure (revue-local runtime) ----------------

class _MockResponse:
    def __init__(self, *, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _MockClient:
    def __init__(self, *, response):
        self._response = response

    def post(self, url, json=None):
        return self._response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_validate_emits_support_footer_on_invalid_licence(
    monkeypatch, tmp_path, capsys
):
    # Arrange — no cache (fresh path skipped) + server rejects the JWT
    from revue_skill.validate import validate_licence

    monkeypatch.setenv(
        "REVUE_LICENCE_CACHE_PATH", str(tmp_path / "licence-cache.json")
    )
    rejected = _MockResponse(
        status_code=200,
        body={"valid": False, "error": "revoked", "message": "licence revoked"},
    )
    monkeypatch.setattr(
        "revue_skill.validate._build_http_client", lambda: _MockClient(response=rejected)
    )

    # Act
    rc = validate_licence("invalid.jwt.token")

    # Assert — rejected licence surfaces the support line (exit 5)
    assert rc == 5
    assert EXPECTED_SUPPORT_LINE in capsys.readouterr().err


def test_validate_does_not_emit_support_footer_on_valid_licence(
    monkeypatch, tmp_path, capsys
):
    # Arrange — no cache + server accepts the JWT
    import time

    from revue_skill.validate import validate_licence

    monkeypatch.setenv(
        "REVUE_LICENCE_CACHE_PATH", str(tmp_path / "licence-cache.json")
    )
    accepted = _MockResponse(
        status_code=200,
        body={
            "valid": True,
            "tier": "indie",
            "paywall_state": None,
            "refresh_after_ts": int(time.time()) + 86400,
            "refreshed_jwt": None,
        },
    )
    monkeypatch.setattr(
        "revue_skill.validate._build_http_client", lambda: _MockClient(response=accepted)
    )

    # Act
    rc = validate_licence("valid.jwt.token")

    # Assert — happy path stays quiet
    assert rc == 0
    assert EXPECTED_SUPPORT_LINE not in capsys.readouterr().err


# ---------- licence gate (revue-local before activation) --------------------

def test_licence_gate_emits_support_footer_when_licence_missing(
    monkeypatch, tmp_path, capsys
):
    # Arrange — first run: a review subcommand, but no licence.jwt exists yet
    from revue_skill.skill.local_run import _gate_licence_validation

    monkeypatch.delenv("REVUE_SKIP_LICENCE_CHECK", raising=False)
    monkeypatch.setattr(
        "revue_skill.skill.local_run.Path.home", lambda: tmp_path
    )

    # Act
    rc = _gate_licence_validation("prepare")

    # Assert — most common first-run failure points the user at support
    assert rc == 8
    assert EXPECTED_SUPPORT_LINE in capsys.readouterr().err


def test_licence_gate_emits_support_footer_when_licence_empty(
    monkeypatch, tmp_path, capsys
):
    # Arrange — licence file exists but is empty
    from revue_skill.skill.local_run import _gate_licence_validation

    monkeypatch.delenv("REVUE_SKIP_LICENCE_CHECK", raising=False)
    licence_dir = tmp_path / ".config" / "revue"
    licence_dir.mkdir(parents=True)
    (licence_dir / "licence.jwt").write_text("")
    monkeypatch.setattr(
        "revue_skill.skill.local_run.Path.home", lambda: tmp_path
    )

    # Act
    rc = _gate_licence_validation("prepare")

    # Assert
    assert rc == 8
    assert EXPECTED_SUPPORT_LINE in capsys.readouterr().err


# ---------- packaging-bug guards (REVUE-359 /code-review high follow-up) ----

def test_emit_support_footer_crashes_loudly_if_support_module_missing(monkeypatch):
    """Closes the silent-packaging-bug gap: the prior tolerant ``except
    ImportError: return`` would have swallowed ``support.py`` being dropped
    from the wheel (the exact REVUE-359 regression). Now the import is hard,
    so a packaging slip surfaces as a loud ImportError instead of a silently
    missing footer.
    """
    import sys

    # Arrange — force ``revue_skill.support`` to look absent on next import
    from revue_skill.skill.local_run import _emit_support_footer

    monkeypatch.setitem(sys.modules, "revue_skill.support", None)

    # Act / Assert
    with pytest.raises(ImportError):
        _emit_support_footer()


# ---------- BaseException passthrough at the cli.main boundary --------------
# The cli.main `except Exception` is intentionally NOT `except BaseException`
# — KeyboardInterrupt (Ctrl-C) and SystemExit must propagate unmodified so
# they keep their native exit semantics (130 for SIGINT; the explicit code
# for sys.exit). A future maintainer who "tightens error handling" by
# widening the catch must fail these tests.


def test_cli_main_propagates_keyboardinterrupt_without_remapping(monkeypatch):
    # Arrange — a subcommand that raises KeyboardInterrupt mid-execution
    from revue_skill import cli

    def _interrupt(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("revue_skill.cli.cmd_version", _interrupt)

    # Act / Assert — KeyboardInterrupt MUST propagate; not remapped to exit 1
    # with a support footer, which would change Ctrl-C's exit-130 contract.
    with pytest.raises(KeyboardInterrupt):
        cli.main(["version"])


def test_cli_main_propagates_systemexit_without_remapping(monkeypatch, capsys):
    # Arrange — a subcommand that raises SystemExit(N) explicitly
    from revue_skill import cli

    def _sysexit(_args):
        raise SystemExit(3)

    monkeypatch.setattr("revue_skill.cli.cmd_version", _sysexit)

    # Act / Assert — SystemExit propagates with its original code; the
    # boundary does NOT swallow it into ``return 1`` with a footer.
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["version"])
    assert exc_info.value.code == 3
    # And: no support footer was emitted on the way out (the user explicitly
    # exited with code 3; this is not a failure routed to support).
    assert EXPECTED_SUPPORT_LINE not in capsys.readouterr().err


# ---------- docs: README + SKILL.md -----------------------------------------

def test_readme_support_section_lists_email_and_issues_note():
    # Arrange
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    # Assert — Support section with the email and a "GitHub issues coming soon" note
    assert "## Support" in readme
    assert "support@revue.sh" in readme
    assert "issues coming soon" in readme.lower()


def test_skill_md_troubleshooting_section_includes_support_note():
    # Arrange
    skill_md = (
        REPO_ROOT / ".claude" / "skills" / "revue" / "SKILL.md"
    ).read_text(encoding="utf-8")

    # Assert — Troubleshooting section pointing failures at support
    assert "Troubleshooting" in skill_md
    assert "support@revue.sh" in skill_md

"""Unit tests for ``revue_skill.skill.emit_usage`` (REVUE-279 code-review fixes).

Covers:
- ``_get_licence_jwt`` honours ``REVUE_LICENCE_PATH`` env override (Fix 5)
- ``_get_licence_jwt`` returns None on non-UTF-8 bytes instead of raising
  ``UnicodeDecodeError`` (Fix 2)
- ``_build_http_client`` uses the same separate-connect-timeout pattern as
  ``validate._build_http_client`` (Fix 6)
"""
from __future__ import annotations

from pathlib import Path

import httpx


def test_get_licence_jwt_honours_env_override(monkeypatch, tmp_path):
    """Fix 5: ``REVUE_LICENCE_PATH`` overrides the default
    ``~/.config/revue/licence.jwt`` path."""
    fake = tmp_path / "custom-licence.jwt"
    fake.write_text("custom.jwt.value")
    monkeypatch.setenv("REVUE_LICENCE_PATH", str(fake))

    from revue_skill.skill.emit_usage import _get_licence_jwt

    assert _get_licence_jwt() == "custom.jwt.value"


def test_get_licence_jwt_returns_none_for_missing_file(monkeypatch, tmp_path):
    """Baseline: missing licence file returns None (not raising)."""
    monkeypatch.setenv("REVUE_LICENCE_PATH", str(tmp_path / "does-not-exist.jwt"))

    from revue_skill.skill.emit_usage import _get_licence_jwt

    assert _get_licence_jwt() is None


def test_get_licence_jwt_returns_none_for_non_utf8_bytes(monkeypatch, tmp_path):
    """Fix 2: a corrupted licence file containing non-UTF-8 bytes must NOT
    propagate ``UnicodeDecodeError`` — return None so the best-effort
    telemetry path silently degrades."""
    fake = tmp_path / "garbage-licence.jwt"
    # Raw write — bypass write_text so we get real non-UTF-8 bytes.
    fake.write_bytes(b"\x80\x81\x82\x83")
    monkeypatch.setenv("REVUE_LICENCE_PATH", str(fake))

    from revue_skill.skill.emit_usage import _get_licence_jwt

    # Must return None, not raise. Pre-fix this raised UnicodeDecodeError
    # because the exception is a ValueError subclass — not caught by the
    # OSError tuple.
    assert _get_licence_jwt() is None


def test_get_licence_jwt_default_path_when_env_unset(monkeypatch, tmp_path):
    """Baseline: with no env override, the function reads from the
    default ``~/.config/revue/licence.jwt`` path (via Path.home)."""
    # Repoint Path.home so we don't touch the real home dir.
    monkeypatch.delenv("REVUE_LICENCE_PATH", raising=False)
    monkeypatch.setattr(
        "revue_skill.skill.emit_usage.Path.home", lambda: tmp_path
    )
    licence = tmp_path / ".config" / "revue" / "licence.jwt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("default.jwt.value")

    from revue_skill.skill.emit_usage import _get_licence_jwt

    assert _get_licence_jwt() == "default.jwt.value"


def test_build_http_client_uses_separate_connect_timeout():
    """Fix 6: ``_build_http_client`` uses ``httpx.Timeout(30.0, connect=10.0)``
    — same shape validate.py uses. Pre-fix the call site used a single
    5-second timeout that lumped connect + read together."""
    from revue_skill.skill.emit_usage import _build_http_client

    client = _build_http_client()
    try:
        timeout = client.timeout
        # httpx.Timeout exposes connect/read/write/pool as floats
        assert timeout.connect == 10.0
        assert timeout.read == 30.0
    finally:
        client.close()

"""Unit tests for ``revue_skill.skill.emit_usage``.

Covers:
- The fixed ``~/.config/revue/licence.jwt`` path.
- ``_get_licence_jwt`` returns None on non-UTF-8 bytes instead of raising
  ``UnicodeDecodeError`` (Fix 2)
- ``_build_http_client`` uses the same separate-connect-timeout pattern as
  ``validate._build_http_client`` (Fix 6)
"""
from __future__ import annotations

def test_emit_usage_ignores_revue_licence_path(monkeypatch, tmp_path):
    """Telemetry reads the fixed path even when the unsupported override is set."""
    # Arrange
    default_path = tmp_path / ".config" / "revue" / "licence.jwt"
    default_path.parent.mkdir(parents=True)
    default_path.write_text("default.jwt.value")
    unsupported_path = tmp_path / "unsupported" / "custom.jwt"
    unsupported_path.parent.mkdir(parents=True)
    unsupported_path.write_text("unsupported.jwt.value")
    monkeypatch.setenv("REVUE_LICENCE_PATH", str(unsupported_path))
    monkeypatch.setattr("revue_skill.skill.emit_usage.Path.home", lambda: tmp_path)

    from revue_skill.skill.emit_usage import _get_licence_jwt

    # Act
    token = _get_licence_jwt()

    # Assert
    assert token == "default.jwt.value"


def test_get_licence_jwt_returns_none_for_missing_file(monkeypatch, tmp_path):
    """Baseline: missing licence file returns None (not raising)."""
    # Arrange
    monkeypatch.setattr("revue_skill.skill.emit_usage.Path.home", lambda: tmp_path)

    from revue_skill.skill.emit_usage import _get_licence_jwt

    # Act
    token = _get_licence_jwt()

    # Assert
    assert token is None


def test_get_licence_jwt_returns_none_for_non_utf8_bytes(monkeypatch, tmp_path):
    """Fix 2: a corrupted licence file containing non-UTF-8 bytes must NOT
    propagate ``UnicodeDecodeError`` — return None so the best-effort
    telemetry path silently degrades."""
    # Arrange
    fake = tmp_path / ".config" / "revue" / "licence.jwt"
    fake.parent.mkdir(parents=True)
    # Raw write — bypass write_text so we get real non-UTF-8 bytes.
    fake.write_bytes(b"\x80\x81\x82\x83")
    monkeypatch.setattr("revue_skill.skill.emit_usage.Path.home", lambda: tmp_path)

    from revue_skill.skill.emit_usage import _get_licence_jwt

    # Act
    token = _get_licence_jwt()

    # Assert
    assert token is None


def test_get_licence_jwt_default_path_when_env_unset(monkeypatch, tmp_path):
    """Baseline: with no env override, the function reads from the
    default ``~/.config/revue/licence.jwt`` path (via Path.home)."""
    # Arrange
    monkeypatch.setattr(
        "revue_skill.skill.emit_usage.Path.home", lambda: tmp_path
    )
    licence = tmp_path / ".config" / "revue" / "licence.jwt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("default.jwt.value")

    from revue_skill.skill.emit_usage import _get_licence_jwt

    # Act
    token = _get_licence_jwt()

    # Assert
    assert token == "default.jwt.value"


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

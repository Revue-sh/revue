"""REVUE-364 — CLI funnel_telemetry module tests.

AC1: events are emitted for install/activate/review
AC2: REVUE_TELEMETRY_OFF=1 suppresses all funnel events; billing unaffected
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from revue_skill.funnel_telemetry import (
    FUNNEL_URL,
    emit_funnel_event,
    get_or_create_install_id,
    is_telemetry_enabled,
)


# ── is_telemetry_enabled ────────────────────────────────────────────────────

def test_telemetry_enabled_by_default(monkeypatch):
    monkeypatch.delenv("REVUE_TELEMETRY_OFF", raising=False)
    assert is_telemetry_enabled() is True


def test_telemetry_disabled_by_env(monkeypatch):
    monkeypatch.setenv("REVUE_TELEMETRY_OFF", "1")
    assert is_telemetry_enabled() is False


def test_telemetry_not_disabled_by_zero(monkeypatch):
    monkeypatch.setenv("REVUE_TELEMETRY_OFF", "0")
    assert is_telemetry_enabled() is True


# ── get_or_create_install_id ─────────────────────────────────────────────────

def test_get_or_create_install_id_mints_uuid(tmp_path, monkeypatch):
    monkeypatch.setattr("revue_skill.funnel_telemetry.Path.home", lambda: tmp_path)
    # Re-import to pick up patched Path.home
    import importlib
    import revue_skill.funnel_telemetry as ft
    importlib.reload(ft)

    install_id = ft.get_or_create_install_id()
    assert install_id is not None
    # Valid UUID4 format
    uuid.UUID(install_id, version=4)


def test_get_or_create_install_id_persists(tmp_path, monkeypatch):
    monkeypatch.setattr("revue_skill.funnel_telemetry.Path.home", lambda: tmp_path)
    import importlib
    import revue_skill.funnel_telemetry as ft
    importlib.reload(ft)

    id1 = ft.get_or_create_install_id()
    id2 = ft.get_or_create_install_id()
    assert id1 == id2


def test_get_or_create_install_id_reads_existing(tmp_path, monkeypatch):
    monkeypatch.setattr("revue_skill.funnel_telemetry.Path.home", lambda: tmp_path)
    import importlib
    import revue_skill.funnel_telemetry as ft
    importlib.reload(ft)

    install_dir = tmp_path / ".config" / "revue"
    install_dir.mkdir(parents=True)
    existing_id = "aabbccdd-1234-5678-aaaa-bbbbccccdddd"
    (install_dir / "install_id").write_text(existing_id)

    result = ft.get_or_create_install_id()
    assert result == existing_id


# ── emit_funnel_event ────────────────────────────────────────────────────────

def test_emit_funnel_event_skipped_when_opt_out(monkeypatch):
    """AC2: REVUE_TELEMETRY_OFF=1 must suppress all funnel events."""
    monkeypatch.setenv("REVUE_TELEMETRY_OFF", "1")
    with patch("revue_skill.funnel_telemetry._httpx") as mock_httpx:
        emit_funnel_event("install")
    mock_httpx.Client.assert_not_called()


def test_emit_funnel_event_posts_correct_fields(tmp_path, monkeypatch):
    """AC1: install event is POSTed with correct event_type and install_id."""
    monkeypatch.delenv("REVUE_TELEMETRY_OFF", raising=False)
    monkeypatch.setattr("revue_skill.funnel_telemetry.Path.home", lambda: tmp_path)

    import importlib
    import revue_skill.funnel_telemetry as ft
    importlib.reload(ft)

    mock_response = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_response)

    with patch("revue_skill.funnel_telemetry._httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        mock_httpx.Timeout = MagicMock(return_value=object())
        ft.emit_funnel_event("install")

    posted = mock_client.post.call_args
    assert posted is not None
    url, kwargs = posted[0][0], posted[1]
    assert url == FUNNEL_URL
    body = kwargs["json"]
    assert body["event_type"] == "install"
    assert "install_id" in body
    assert "ts" in body


def test_emit_funnel_event_with_key(tmp_path, monkeypatch):
    """AC1: activate event carries the licence key."""
    monkeypatch.delenv("REVUE_TELEMETRY_OFF", raising=False)
    monkeypatch.setattr("revue_skill.funnel_telemetry.Path.home", lambda: tmp_path)

    import importlib
    import revue_skill.funnel_telemetry as ft
    importlib.reload(ft)

    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("revue_skill.funnel_telemetry._httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        mock_httpx.Timeout = MagicMock(return_value=object())
        ft.emit_funnel_event("activate", key="lic_testkey123")

    body = mock_client.post.call_args[1]["json"]
    assert body["event_type"] == "activate"
    assert body["key"] == "lic_testkey123"


def test_emit_funnel_event_silent_on_network_error(tmp_path, monkeypatch):
    """Best-effort: network errors must never raise."""
    monkeypatch.delenv("REVUE_TELEMETRY_OFF", raising=False)
    monkeypatch.setattr("revue_skill.funnel_telemetry.Path.home", lambda: tmp_path)

    import importlib
    import revue_skill.funnel_telemetry as ft
    importlib.reload(ft)

    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = ConnectionError("network down")

    with patch("revue_skill.funnel_telemetry._httpx") as mock_httpx:
        mock_httpx.Client.return_value = mock_client
        mock_httpx.Timeout = MagicMock(return_value=object())
        # Must not raise
        ft.emit_funnel_event("install")


def test_emit_funnel_event_silent_when_httpx_missing(monkeypatch):
    """Best-effort: missing httpx must not raise."""
    monkeypatch.delenv("REVUE_TELEMETRY_OFF", raising=False)
    with patch("revue_skill.funnel_telemetry._httpx", None):
        emit_funnel_event("install")  # must not raise

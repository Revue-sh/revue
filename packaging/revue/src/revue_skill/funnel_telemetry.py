"""REVUE-364: anonymous install→activate→review funnel telemetry.

Emits best-effort POST /funnel/event events to the server. All operations
are silent-fail — a network error or missing install_id never blocks the
caller.

Opt-out: set REVUE_TELEMETRY_OFF=1 to suppress all funnel events. This
gate applies ONLY to funnel analytics; billing counters (POST /usage/track,
POST /api/v2/usage/emit) are unaffected and always run.

install_id: a random UUID4 minted once and stored in
~/.config/revue/install_id. It is NOT PII — no email, name, or repo path.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Final

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

FUNNEL_URL: Final[str] = "https://revue.sh/funnel/event"
_INSTALL_ID_FILE: Final[Path] = Path.home() / ".config" / "revue" / "install_id"


def is_telemetry_enabled() -> bool:
    """Return False when the user has opted out via REVUE_TELEMETRY_OFF=1."""
    return os.environ.get("REVUE_TELEMETRY_OFF", "").strip() != "1"


def get_or_create_install_id() -> str | None:
    """Return the persistent anonymous install_id, minting it on first call.

    Stored at ~/.config/revue/install_id as a plain UUID4 string. Returns
    None on any filesystem error so callers can skip the event gracefully.
    """
    try:
        if _INSTALL_ID_FILE.exists():
            value = _INSTALL_ID_FILE.read_text().strip()
            if value:
                return value
        # Mint a fresh install_id and persist it.
        _INSTALL_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        install_id = str(uuid.uuid4())
        _INSTALL_ID_FILE.write_text(install_id)
        return install_id
    except (OSError, PermissionError):
        return None


def emit_funnel_event(event_type: str, key: str = "") -> None:
    """Post one funnel event to the server. Best-effort: never raises.

    Args:
        event_type: One of 'install', 'activate', 'review'.
        key: Optional licence key (empty string for install events).
    """
    if not is_telemetry_enabled():
        return

    if _httpx is None:
        return

    install_id = get_or_create_install_id()
    if not install_id:
        return

    payload = {
        "event_type": event_type,
        "install_id": install_id,
        "key": key,
        "ts": int(time.time()),
    }

    try:
        with _httpx.Client(timeout=_httpx.Timeout(5.0, connect=3.0)) as client:
            client.post(FUNNEL_URL, json=payload)
    except Exception:  # noqa: BLE001 — best-effort; never block the caller
        pass

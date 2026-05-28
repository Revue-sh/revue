"""Emit usage telemetry to the server (REVUE-279 Task 5).

Records per-invocation usage (reviews_run, findings_count, emitted_at) via
POST /api/v2/usage/emit. Best-effort operation: any failure (network, auth,
server) is logged but never blocks the review.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


def _get_licence_path() -> Path:
    """Return the path to the licence JWT file. Test-overridable via
    REVUE_LICENCE_PATH env var; production uses ~/.config/revue/licence.jwt.

    Mirrors the REVUE_LICENCE_CACHE_PATH override in validate.py."""
    env_override = os.environ.get("REVUE_LICENCE_PATH")
    if env_override:
        return Path(env_override)
    return Path.home() / ".config" / "revue" / "licence.jwt"


def _get_licence_jwt() -> str | None:
    """Read the licence JWT from the configured licence path.

    Returns None if the file doesn't exist, can't be read, or contains
    non-UTF-8 bytes (UnicodeDecodeError is not an OSError subclass so it
    must be listed explicitly — REVUE-279 code-review fix)."""
    licence_file = _get_licence_path()
    try:
        return licence_file.read_text().strip()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


def _build_http_client() -> "httpx.Client":
    """Construct the httpx.Client used to POST to /usage/emit.

    Mirrors validate.py's _build_http_client factory: separate connect
    timeout so a slow DNS / TCP handshake doesn't blow the read budget
    (REVUE-279 code-review fix — was a single 5s timeout)."""
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))


def emit_usage(
    findings_count: int,
    emitted_at: int,
    client: "httpx.Client | None" = None,
) -> None:
    """Post per-invocation usage telemetry to /api/v2/usage/emit.

    Args:
        findings_count: Number of findings returned in this invocation
        emitted_at: Unix timestamp when the invocation began (client-supplied)
        client: optional httpx.Client. Tests inject a fake; production omits
            this and _build_http_client() constructs one internally.

    Best-effort: any error (missing JWT, network failure, server error) is
    logged but never blocks the review. The server will emit findings regardless
    of whether telemetry arrives."""
    from revue_core.core.logging_channels import Log

    if httpx is None:
        Log.cli.debug("[revue] emit_usage: httpx not available (best-effort)")
        return

    jwt = _get_licence_jwt()
    if not jwt:
        Log.cli.debug("[revue] emit_usage: no licence JWT found (best-effort)")
        return

    try:
        payload = {
            "jwt": jwt,
            "reviews_run": 1,  # Per-invocation (always 1)
            "findings_count": findings_count,
            "ts": emitted_at,
        }

        owns_client = client is None
        active_client = client if client is not None else _build_http_client()
        try:
            resp = active_client.post(
                "https://revue.sh/api/v2/usage/emit",
                json=payload,
            )
        finally:
            if owns_client:
                active_client.close()

        if resp.status_code != 200:
            Log.cli.debug(
                "[revue] emit_usage: server returned %d (best-effort)", resp.status_code
            )

    except Exception as exc:
        Log.cli.debug(
            "[revue] emit_usage: failed to emit (%s — best-effort)", exc
        )

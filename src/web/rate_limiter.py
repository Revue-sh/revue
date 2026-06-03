"""Rate limiting for the licence activation endpoint (REVUE-325).

Two limits, both with hardcoded constants (NOT env-var overridable — a
configurable security primitive is a bypass exploit):

- Per-IP:  5 requests / 10 minutes
- Per-key: 10 successful activations / 24 hours

Both limits are enforced against the activation database (SQLite), so the
state survives a process or machine restart (AC6). The caller is responsible
for serialising the check-then-write critical section (e.g. ``BEGIN
IMMEDIATE``) so concurrent requests cannot both read a stale count and
overshoot the limit.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

# Hardcoded constants — not env-var overridable (REVUE-325 AC1, AC2, AC6).
PER_IP_LIMIT = 5
PER_IP_WINDOW_MINUTES = 10
PER_KEY_LIMIT = 10
PER_KEY_WINDOW_HOURS = 24

# One persisted flood row / alert per key per this window, so a sustained
# flood does not spam the table or the alerting channel.
_FLOOD_DEDUP_MINUTES = 5

logger = logging.getLogger(__name__)


class ActivationRateLimitError(Exception):
    """Raised when an activation attempt exceeds a rate limit.

    ``scope`` is ``"ip"`` or ``"key"`` so the caller can react differently
    (e.g. emit a flood event only when a *key* is being hammered).
    """

    def __init__(self, reason: str, scope: str, retry_after_seconds: int | None = None):
        self.reason = reason
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


def hash_key(value: str) -> str:
    """Return the SHA-256 hex digest of a key or fingerprint (AC3)."""
    return hashlib.sha256(value.encode()).hexdigest()


def _utcnow() -> datetime:
    """Naive UTC timestamp matching the ``attempted_at`` storage format.

    Always UTC so window comparisons are correct regardless of the host's
    local timezone, and naive (tz-stripped) so the ``.isoformat()`` string
    compares consistently against other rows written the same way.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _retry_after_seconds(oldest_iso: str | None, window: timedelta, full_window_seconds: int) -> int:
    """Seconds until the oldest in-window attempt ages out (RFC-correct Retry-After)."""
    if not oldest_iso:
        return full_window_seconds
    try:
        oldest = datetime.fromisoformat(oldest_iso)
    except ValueError:
        return full_window_seconds
    remaining = int((oldest + window - _utcnow()).total_seconds())
    return max(1, remaining)


def check_ip_rate_limit(conn: sqlite3.Connection, ip_address: str) -> None:
    """AC1: cap activation requests at PER_IP_LIMIT per PER_IP_WINDOW_MINUTES per IP.

    Counts only genuine attempts (``blocked = 0``); throttled 429 responses are
    logged for incident response but excluded from the count so a client cannot
    extend its own lockout simply by retrying.

    Raises ``ActivationRateLimitError(scope="ip")`` when the limit is reached.
    """
    window = timedelta(minutes=PER_IP_WINDOW_MINUTES)
    window_start = (_utcnow() - window).isoformat()
    row = conn.execute(
        """SELECT COUNT(*) AS count, MIN(attempted_at) AS oldest
           FROM activation_attempts
           WHERE ip_address = ? AND blocked = 0 AND attempted_at >= ?""",
        (ip_address, window_start),
    ).fetchone()
    count = row["count"] if row else 0
    if count >= PER_IP_LIMIT:
        logger.warning(
            "Per-IP rate limit exceeded: %s (%d/%d in %dm)",
            ip_address, count, PER_IP_LIMIT, PER_IP_WINDOW_MINUTES,
        )
        raise ActivationRateLimitError(
            f"Too many activation attempts from this IP. "
            f"Max {PER_IP_LIMIT} per {PER_IP_WINDOW_MINUTES} minutes.",
            scope="ip",
            retry_after_seconds=_retry_after_seconds(
                row["oldest"] if row else None, window, PER_IP_WINDOW_MINUTES * 60
            ),
        )


def check_key_rate_limit(conn: sqlite3.Connection, license_key_id: int) -> None:
    """AC2: cap *successful* activations at PER_KEY_LIMIT per PER_KEY_WINDOW_HOURS per key.

    Enforced regardless of source IP. Counter persists in the database (AC6).

    Raises ``ActivationRateLimitError(scope="key")`` when the limit is reached.
    """
    window = timedelta(hours=PER_KEY_WINDOW_HOURS)
    window_start = (_utcnow() - window).isoformat()
    # ``blocked = 0`` is defensive: blocked rows are always is_successful=0 today,
    # but pinning it keeps the per-key count correct if that ever changes.
    row = conn.execute(
        """SELECT COUNT(*) AS count, MIN(attempted_at) AS oldest
           FROM activation_attempts
           WHERE license_key_id = ? AND is_successful = 1 AND blocked = 0
                 AND attempted_at >= ?""",
        (license_key_id, window_start),
    ).fetchone()
    count = row["count"] if row else 0
    if count >= PER_KEY_LIMIT:
        logger.warning(
            "Per-key rate limit exceeded: license_key_id=%d (%d/%d in %dh)",
            license_key_id, count, PER_KEY_LIMIT, PER_KEY_WINDOW_HOURS,
        )
        raise ActivationRateLimitError(
            f"Too many activations for this key. "
            f"Max {PER_KEY_LIMIT} per {PER_KEY_WINDOW_HOURS} hours.",
            scope="key",
            retry_after_seconds=_retry_after_seconds(
                row["oldest"] if row else None, window, PER_KEY_WINDOW_HOURS * 3600
            ),
        )


def log_activation_attempt(
    conn: sqlite3.Connection,
    license_key_id: int | None,
    raw_key: str,
    ip_address: str,
    machine_fingerprint: str,
    is_successful: bool = False,
    blocked: bool = False,
) -> None:
    """AC3: record an activation attempt with hashed key + fingerprint.

    ``license_key_id`` is ``None`` for attempts against keys that do not exist
    (so brute-force probes are still recorded). ``blocked`` marks a 429
    throttle response — logged for incident response but excluded from the
    per-IP count so retries cannot extend a lockout.
    """
    conn.execute(
        """INSERT INTO activation_attempts
           (license_key_id, ip_address, key_hash, fingerprint_hash,
            is_successful, blocked, attempted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            license_key_id,
            ip_address,
            hash_key(raw_key),
            hash_key(machine_fingerprint),
            1 if is_successful else 0,
            1 if blocked else 0,
            _utcnow().isoformat(),
        ),
    )


def emit_flood_event(conn: sqlite3.Connection, license_key_id: int, raw_key: str) -> None:
    """AC4: persist + log a ``licence.activation.flood`` event on a key crossing.

    Called when a key is blocked by the per-key limit (i.e. the attempt *after*
    it crossed PER_KEY_LIMIT successful activations). Deduplicated to one
    persisted row and one alert per ``_FLOOD_DEDUP_MINUTES`` so a sustained
    flood does not spam the table or alerting channel.
    """
    now = _utcnow()
    key_hash = hash_key(raw_key)
    recent = conn.execute(
        """SELECT 1 FROM activation_flood_events
           WHERE license_key_id = ? AND flood_detected_at >= ?""",
        (license_key_id, (now - timedelta(minutes=_FLOOD_DEDUP_MINUTES)).isoformat()),
    ).fetchone()
    if recent:
        return
    conn.execute(
        """INSERT INTO activation_flood_events
           (license_key_id, key_hash, flood_detected_at)
           VALUES (?, ?, ?)""",
        (license_key_id, key_hash, now.isoformat()),
    )
    logger.error(
        "licence.activation.flood",
        extra={
            "event": "licence.activation.flood",
            "key_hash": key_hash,
            "limit": PER_KEY_LIMIT,
            "window_hours": PER_KEY_WINDOW_HOURS,
            "timestamp": now.isoformat(),
        },
    )


def validate_activation_headers(user_agent: str | None, content_type: str | None) -> None:
    """AC5: validate required headers for activation.

    Raises ``ValueError`` if the User-Agent is missing or the Content-Type is
    not ``application/json``.
    """
    if not user_agent:
        raise ValueError("Missing required User-Agent header")
    if not content_type or "application/json" not in content_type:
        raise ValueError("Content-Type must be application/json")

"""SQLite database connection and schema management."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

DATABASE_PATH = os.environ.get("DATABASE_PATH", "revue.db")

# Canonical wall-clock format for ``usage_events.received_at``. Both the
# schema CHECK constraint below and the lex-compare in
# ``count_usage_events_since_month_start`` (models.py) reference this single
# source of truth. Changing one without the other silently breaks the free-tier
# counter — they share one constant so the contract is impossible to forget.
USAGE_RECEIVED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"
USAGE_RECEIVED_AT_GLOB = (
    "[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9] "
    "[0-2][0-9]:[0-5][0-9]:[0-5][0-9]"
)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tier TEXT DEFAULT 'free',
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS license_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    key TEXT UNIQUE NOT NULL,
    tier TEXT NOT NULL DEFAULT 'free',
    reviews_used_this_month INTEGER DEFAULT 0,
    reviews_limit INTEGER DEFAULT 25,
    period_reset_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1,
    -- REVUE-413: Stripe subscription metadata persisted from the webhook.
    -- ``current_period_end`` is the renewal/expiry instant (ISO-8601 UTC string,
    -- derived from the subscription's epoch ``current_period_end``); the Account
    -- → Plan page (REVUE-382) renders it as the renewal line. ``subscription_status``
    -- is the raw Stripe status (active/past_due/unpaid/canceled/...) that drives
    -- the lapsed-vs-free state. Both NULL until the first subscription webhook.
    current_period_end TIMESTAMP,
    subscription_status TEXT
);

CREATE TABLE IF NOT EXISTS review_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key_id INTEGER NOT NULL REFERENCES license_keys(id),
    repo_id TEXT,
    pr_title TEXT,
    pr_number INTEGER,
    ci_run_id TEXT,
    agents_used TEXT,
    findings_count INTEGER DEFAULT 0,
    duration_ms INTEGER,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_runs_license_key ON review_runs(license_key_id);
CREATE INDEX IF NOT EXISTS idx_review_runs_created_at ON review_runs(created_at DESC);

-- REVUE-278: per-invocation usage records emitted by /revue-local. ``emitted_at``
-- is the client-supplied epoch seconds (untrusted, may be skewed); ``received_at``
-- is the server-stamped insertion time used by cohort/billing queries. The
-- composite index supports the REVUE-279 free-tier paywall lookup
-- (workspace_id + recent window).
--
-- REVUE-279 code-review fix: the CHECK constraint pins the format that
-- count_usage_events_since_month_start's lex string comparison depends on.
-- Without it, a future writer using ISO-8601 with 'T' separator, date-only,
-- or integer epoch strings would silently break the WHERE filter — counter
-- undercounts → free-tier bypass.
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    reviews_run INTEGER NOT NULL DEFAULT 0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    emitted_at INTEGER NOT NULL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP CHECK (
        received_at IS NULL OR
        received_at GLOB '{USAGE_RECEIVED_AT_GLOB}'
    )
);

CREATE INDEX IF NOT EXISTS idx_usage_events_workspace_received_at
    ON usage_events(workspace_id, received_at);

-- REVUE-325: Activation attempt tracking for rate limiting.
-- license_key_id is NULLABLE: attempts against keys that do not exist (brute
-- force probes) are still logged with a NULL key id. ``blocked`` marks a 429
-- throttle response — recorded for incident response but excluded from the
-- per-IP count so retries cannot extend a lockout.
CREATE TABLE IF NOT EXISTS activation_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key_id INTEGER REFERENCES license_keys(id),
    ip_address TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    is_successful INTEGER DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_activation_attempts_key_time
    ON activation_attempts(license_key_id, attempted_at);
CREATE INDEX IF NOT EXISTS idx_activation_attempts_ip_time
    ON activation_attempts(ip_address, attempted_at);

-- REVUE-325: Flood event tracking (when key crosses 10 activations in 24h)
CREATE TABLE IF NOT EXISTS activation_flood_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key_id INTEGER NOT NULL REFERENCES license_keys(id),
    key_hash TEXT NOT NULL,
    flood_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_activation_flood_events_key
    ON activation_flood_events(license_key_id);
"""

REVIEWS_LIMIT_BY_TIER: dict[str, int | None] = {
    "free": 25,
    "indie": 100,
    "pro": None,
    "enterprise_starter": None,
    "enterprise_growth": None,
    "enterprise_plus": None,
}


def get_db_path() -> str:
    return os.environ.get("DATABASE_PATH", "revue.db")


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db(db_path: str | None = None) -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


MIGRATIONS_SQL = """
-- Idempotent column additions for review_runs (SQLite doesn't support IF NOT EXISTS for columns)
-- These are safe to run multiple times; they fail silently if the column already exists.
"""

_USERS_MIGRATIONS = [
    ("stripe_subscription_id", "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT"),
    ("referral_source", "ALTER TABLE users ADD COLUMN referral_source TEXT"),
]

_REVIEW_RUNS_MIGRATIONS = [
    ("pr_title", "ALTER TABLE review_runs ADD COLUMN pr_title TEXT"),
    ("pr_number", "ALTER TABLE review_runs ADD COLUMN pr_number INTEGER"),
    ("findings_count", "ALTER TABLE review_runs ADD COLUMN findings_count INTEGER DEFAULT 0"),
    ("findings_by_severity", "ALTER TABLE review_runs ADD COLUMN findings_by_severity TEXT"),
]

_ACTIVATION_ATTEMPTS_MIGRATIONS = [
    # REVUE-325: dev DBs created earlier in this PR predate the ``blocked`` column.
    ("blocked", "ALTER TABLE activation_attempts ADD COLUMN blocked INTEGER DEFAULT 0"),
]

_LICENSE_KEYS_MIGRATIONS = [
    # REVUE-413: persist the Stripe renewal date + raw subscription status so the
    # webhook no longer discards them. Safe/idempotent on existing rows — both
    # default NULL until the next subscription.created/updated event populates them.
    ("current_period_end", "ALTER TABLE license_keys ADD COLUMN current_period_end TIMESTAMP"),
    ("subscription_status", "ALTER TABLE license_keys ADD COLUMN subscription_status TEXT"),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply idempotent column migrations. SQLite doesn't support ADD COLUMN IF NOT EXISTS.

    Each block guards on the target table actually existing (``if <table>_cols``)
    so a partial schema — e.g. a fixture DB holding only license_keys — does not
    crash the run with ``no such table``.
    """
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    for col_name, sql in _USERS_MIGRATIONS:
        if user_cols and col_name not in user_cols:
            conn.execute(sql)

    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_runs)").fetchall()}
    for col_name, sql in _REVIEW_RUNS_MIGRATIONS:
        if run_cols and col_name not in run_cols:
            conn.execute(sql)

    attempt_cols = {row[1] for row in conn.execute("PRAGMA table_info(activation_attempts)").fetchall()}
    for col_name, sql in _ACTIVATION_ATTEMPTS_MIGRATIONS:
        if attempt_cols and col_name not in attempt_cols:
            conn.execute(sql)

    lk_cols = {row[1] for row in conn.execute("PRAGMA table_info(license_keys)").fetchall()}
    for col_name, sql in _LICENSE_KEYS_MIGRATIONS:
        if lk_cols and col_name not in lk_cols:
            conn.execute(sql)


def init_db(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _run_migrations(conn)

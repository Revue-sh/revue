"""SQLite database connection and schema management."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

DATABASE_PATH = os.environ.get("DATABASE_PATH", "revue.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tier TEXT DEFAULT 'free',
    stripe_customer_id TEXT,
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
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS review_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key_id INTEGER NOT NULL REFERENCES license_keys(id),
    repo_id TEXT,
    ci_run_id TEXT,
    agents_used TEXT,
    duration_ms INTEGER,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
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


def init_db(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)

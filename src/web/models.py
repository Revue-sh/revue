"""SQLite data access functions (no ORM)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class User:
    id: int
    email: str
    password_hash: str
    created_at: str
    tier: str
    stripe_customer_id: Optional[str]
    is_active: bool


@dataclass
class Workspace:
    id: int
    user_id: int
    name: str
    created_at: str


@dataclass
class LicenseKey:
    id: int
    workspace_id: int
    key: str
    tier: str
    reviews_used_this_month: int
    reviews_limit: Optional[int]
    period_reset_at: Optional[str]
    created_at: str
    is_active: bool


@dataclass
class ReviewRun:
    id: int
    license_key_id: int
    repo_id: Optional[str]
    ci_run_id: Optional[str]
    agents_used: list[str]
    duration_ms: Optional[int]
    status: str
    created_at: str


def row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        tier=row["tier"],
        stripe_customer_id=row["stripe_customer_id"],
        is_active=bool(row["is_active"]),
    )


def row_to_license_key(row: sqlite3.Row) -> LicenseKey:
    return LicenseKey(
        id=row["id"],
        workspace_id=row["workspace_id"],
        key=row["key"],
        tier=row["tier"],
        reviews_used_this_month=row["reviews_used_this_month"],
        reviews_limit=row["reviews_limit"],
        period_reset_at=row["period_reset_at"],
        created_at=row["created_at"],
        is_active=bool(row["is_active"]),
    )


def row_to_review_run(row: sqlite3.Row) -> ReviewRun:
    agents_raw = row["agents_used"]
    agents = json.loads(agents_raw) if agents_raw else []
    return ReviewRun(
        id=row["id"],
        license_key_id=row["license_key_id"],
        repo_id=row["repo_id"],
        ci_run_id=row["ci_run_id"],
        agents_used=agents,
        duration_ms=row["duration_ms"],
        status=row["status"],
        created_at=row["created_at"],
    )


# --- User queries ---

def create_user(conn: sqlite3.Connection, email: str, password_hash: str) -> int:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, password_hash),
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_user_by_email(conn: sqlite3.Connection, email: str) -> Optional[User]:
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return row_to_user(row) if row else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[User]:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_user(row) if row else None


# --- Workspace queries ---

def create_workspace(conn: sqlite3.Connection, user_id: int, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO workspaces (user_id, name) VALUES (?, ?)",
        (user_id, name),
    )
    return cur.lastrowid  # type: ignore[return-value]


# --- License key queries ---

def _next_month_first() -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if now.month == 12:
        return datetime(now.year + 1, 1, 1).isoformat()
    return datetime(now.year, now.month + 1, 1).isoformat()


def create_license_key(
    conn: sqlite3.Connection,
    workspace_id: int,
    key: str,
    tier: str = "free",
    reviews_limit: Optional[int] = 25,
) -> int:
    next_reset = _next_month_first()
    cur = conn.execute(
        """INSERT INTO license_keys
           (workspace_id, key, tier, reviews_limit, period_reset_at)
           VALUES (?, ?, ?, ?, ?)""",
        (workspace_id, key, tier, reviews_limit, next_reset),
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_license_by_key(conn: sqlite3.Connection, key: str) -> Optional[LicenseKey]:
    row = conn.execute("SELECT * FROM license_keys WHERE key = ?", (key,)).fetchone()
    return row_to_license_key(row) if row else None


def get_license_for_user(conn: sqlite3.Connection, user_id: int) -> Optional[LicenseKey]:
    row = conn.execute(
        """SELECT lk.* FROM license_keys lk
           JOIN workspaces w ON lk.workspace_id = w.id
           WHERE w.user_id = ? AND lk.is_active = 1
           ORDER BY lk.created_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    return row_to_license_key(row) if row else None


def increment_usage(conn: sqlite3.Connection, license_key_id: int) -> None:
    conn.execute(
        "UPDATE license_keys SET reviews_used_this_month = reviews_used_this_month + 1 WHERE id = ?",
        (license_key_id,),
    )


def reset_monthly_counter(conn: sqlite3.Connection, license_key_id: int) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute(
        "UPDATE license_keys SET reviews_used_this_month = 0, period_reset_at = ? WHERE id = ?",
        (now, license_key_id),
    )


# --- Review run queries ---

def create_review_run(
    conn: sqlite3.Connection,
    license_key_id: int,
    repo_id: Optional[str] = None,
    ci_run_id: Optional[str] = None,
    agents_used: Optional[list[str]] = None,
    duration_ms: Optional[int] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO review_runs
           (license_key_id, repo_id, ci_run_id, agents_used, duration_ms)
           VALUES (?, ?, ?, ?, ?)""",
        (license_key_id, repo_id, ci_run_id, json.dumps(agents_used or []), duration_ms),
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_recent_reviews(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> list[ReviewRun]:
    rows = conn.execute(
        """SELECT rr.* FROM review_runs rr
           JOIN license_keys lk ON rr.license_key_id = lk.id
           JOIN workspaces w ON lk.workspace_id = w.id
           WHERE w.user_id = ?
           ORDER BY rr.created_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [row_to_review_run(r) for r in rows]

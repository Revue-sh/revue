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
    stripe_subscription_id: Optional[str]
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
    pr_title: Optional[str]
    pr_number: Optional[int]
    ci_run_id: Optional[str]
    agents_used: list[str]
    findings_count: int
    findings_by_severity: dict  # {"critical": 0, "high": 0, "medium": 0, "low": 0}
    duration_ms: Optional[int]
    status: str
    created_at: str


def row_to_user(row: sqlite3.Row) -> User:
    keys = row.keys()
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        tier=row["tier"],
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"] if "stripe_subscription_id" in keys else None,
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


_DEFAULT_SEVERITY: dict = {"critical": 0, "high": 0, "medium": 0, "low": 0}


def row_to_review_run(row: sqlite3.Row) -> ReviewRun:
    agents_raw = row["agents_used"]
    agents = json.loads(agents_raw) if agents_raw else []
    keys = row.keys()
    sev_raw = row["findings_by_severity"] if "findings_by_severity" in keys else None
    severity = json.loads(sev_raw) if sev_raw else dict(_DEFAULT_SEVERITY)
    return ReviewRun(
        id=row["id"],
        license_key_id=row["license_key_id"],
        repo_id=row["repo_id"],
        pr_title=row["pr_title"] if "pr_title" in keys else None,
        pr_number=row["pr_number"] if "pr_number" in keys else None,
        ci_run_id=row["ci_run_id"],
        agents_used=agents,
        findings_count=row["findings_count"] if "findings_count" in keys else 0,
        findings_by_severity=severity,
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
    pr_title: Optional[str] = None,
    pr_number: Optional[int] = None,
    ci_run_id: Optional[str] = None,
    agents_used: Optional[list[str]] = None,
    findings_count: int = 0,
    findings_by_severity: Optional[dict] = None,
    duration_ms: Optional[int] = None,
) -> int:
    sev = findings_by_severity or {"critical": 0, "high": 0, "medium": 0, "low": 0}
    cur = conn.execute(
        """INSERT INTO review_runs
           (license_key_id, repo_id, pr_title, pr_number, ci_run_id, agents_used,
            findings_count, findings_by_severity, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (license_key_id, repo_id, pr_title, pr_number, ci_run_id,
         json.dumps(agents_used or []), findings_count, json.dumps(sev), duration_ms),
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


def get_analytics(
    conn: sqlite3.Connection,
    user_id: int,
    days: int = 30,
) -> dict:
    """Return aggregate analytics for a user over the last N days.

    Returns:
        {
          "reviews_over_time":   [{"date": "YYYY-MM-DD", "count": N, "findings": N}],
          "severity_totals":     {"critical": N, "high": N, "medium": N, "low": N},
          "top_repos":           [{"repo_id": str, "reviews": N, "findings": N}],
          "status_breakdown":    {"completed": N, "failed": N, "skipped": N},
          "total_reviews":       N,
          "total_findings":      N,
          "avg_duration_ms":     N,
          "period_days":         N,
        }
    """
    rows = conn.execute(
        """SELECT rr.created_at, rr.findings_count, rr.findings_by_severity,
                  rr.repo_id, rr.status, rr.duration_ms
           FROM review_runs rr
           JOIN license_keys lk ON rr.license_key_id = lk.id
           JOIN workspaces w ON lk.workspace_id = w.id
           WHERE w.user_id = ?
             AND rr.created_at >= datetime('now', ?)
           ORDER BY rr.created_at ASC""",
        (user_id, f"-{days} days"),
    ).fetchall()

    # Aggregate
    reviews_by_date: dict[str, dict] = {}
    severity_totals = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    repos: dict[str, dict] = {}
    status_counts: dict[str, int] = {}
    total_findings = 0
    total_duration = 0
    duration_count = 0

    for row in rows:
        date = str(row["created_at"])[:10]
        findings = row["findings_count"] or 0
        status = row["status"] or "completed"
        repo = row["repo_id"] or "unknown"
        dur = row["duration_ms"]

        # Reviews over time
        if date not in reviews_by_date:
            reviews_by_date[date] = {"date": date, "count": 0, "findings": 0}
        reviews_by_date[date]["count"] += 1
        reviews_by_date[date]["findings"] += findings

        # Severity
        sev_raw = row["findings_by_severity"]
        if sev_raw:
            try:
                sev = json.loads(sev_raw)
                for k in severity_totals:
                    severity_totals[k] += sev.get(k, 0)
            except (json.JSONDecodeError, TypeError):
                pass

        # Repos
        if repo not in repos:
            repos[repo] = {"repo_id": repo, "reviews": 0, "findings": 0}
        repos[repo]["reviews"] += 1
        repos[repo]["findings"] += findings

        # Status
        status_counts[status] = status_counts.get(status, 0) + 1

        total_findings += findings
        if dur:
            total_duration += dur
            duration_count += 1

    top_repos = sorted(repos.values(), key=lambda r: r["findings"], reverse=True)[:5]
    avg_duration = total_duration // duration_count if duration_count else 0

    return {
        "reviews_over_time": list(reviews_by_date.values()),
        "severity_totals": severity_totals,
        "top_repos": top_repos,
        "status_breakdown": {
            "completed": status_counts.get("completed", 0),
            "failed": status_counts.get("failed", 0),
            "skipped": status_counts.get("skipped", 0),
        },
        "total_reviews": len(rows),
        "total_findings": total_findings,
        "avg_duration_ms": avg_duration,
        "period_days": days,
    }


def get_user_by_stripe_customer(conn: sqlite3.Connection, customer_id: str) -> Optional[User]:
    row = conn.execute(
        "SELECT * FROM users WHERE stripe_customer_id = ?", (customer_id,)
    ).fetchone()
    return row_to_user(row) if row else None


def update_user_tier(conn: sqlite3.Connection, user_id: int, tier: str) -> None:
    from database import REVIEWS_LIMIT_BY_TIER
    conn.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, user_id))
    # Sync license key limit for this user
    limit = REVIEWS_LIMIT_BY_TIER.get(tier)
    conn.execute(
        """UPDATE license_keys SET tier = ?, reviews_limit = ?
           WHERE workspace_id IN (SELECT id FROM workspaces WHERE user_id = ?)""",
        (tier, limit, user_id),
    )


def update_stripe_customer_id(
    conn: sqlite3.Connection, user_id: int, customer_id: str
) -> None:
    conn.execute(
        "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
        (customer_id, user_id),
    )


def update_stripe_subscription_id(
    conn: sqlite3.Connection, user_id: int, subscription_id: str
) -> None:
    conn.execute(
        "UPDATE users SET stripe_subscription_id = ? WHERE id = ?",
        (subscription_id, user_id),
    )


def get_all_runs_for_user(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
    repo_id: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[list[ReviewRun], int]:
    """Return paginated runs + total count for a user. Used by /api/runs and /runs page."""
    filters = ["w.user_id = ?"]
    params: list = [user_id]
    if repo_id:
        filters.append("rr.repo_id = ?")
        params.append(repo_id)
    if status:
        filters.append("rr.status = ?")
        params.append(status)
    where = " AND ".join(filters)

    total = conn.execute(
        f"""SELECT COUNT(*) FROM review_runs rr
            JOIN license_keys lk ON rr.license_key_id = lk.id
            JOIN workspaces w ON lk.workspace_id = w.id
            WHERE {where}""",
        params,
    ).fetchone()[0]

    rows = conn.execute(
        f"""SELECT rr.* FROM review_runs rr
            JOIN license_keys lk ON rr.license_key_id = lk.id
            JOIN workspaces w ON lk.workspace_id = w.id
            WHERE {where}
            ORDER BY rr.created_at DESC LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    return [row_to_review_run(r) for r in rows], total

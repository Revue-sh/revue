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
    referral_source: Optional[str] = None


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
    # REVUE-413: Stripe renewal date (ISO-8601 UTC) + raw subscription status,
    # persisted from the subscription webhook. Optional — NULL on legacy/pre-
    # migration rows and until the first subscription event arrives.
    current_period_end: Optional[str] = None
    subscription_status: Optional[str] = None
    # REVUE-382: UTC naive-isoformat timestamp of the last SUCCESSFUL licence
    # validation. NULL until the first /v2/licence/validate succeeds.
    last_validated_at: Optional[str] = None


@dataclass
class UsageEvent:
    id: int
    workspace_id: int
    reviews_run: int
    findings_count: int
    emitted_at: int  # client-supplied epoch seconds (untrusted clock)
    received_at: str  # server-stamped ISO8601 (canonical timeline)


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
        referral_source=row["referral_source"] if "referral_source" in keys else None,
    )


def row_to_license_key(row: sqlite3.Row) -> LicenseKey:
    keys = row.keys()
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
        # Defensive read (like row_to_user): a pre-migration row lacks these
        # columns, so guard with ``in keys`` rather than a bare row[...] that
        # would raise on the missing column. REVUE-413.
        current_period_end=row["current_period_end"] if "current_period_end" in keys else None,
        subscription_status=row["subscription_status"] if "subscription_status" in keys else None,
        last_validated_at=row["last_validated_at"] if "last_validated_at" in keys else None,
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

def create_user(
    conn: sqlite3.Connection,
    email: str,
    password_hash: str,
    referral_source: Optional[str] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, referral_source) VALUES (?, ?, ?)",
        (email, password_hash, referral_source),
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


def get_active_license_for_workspace(
    conn: sqlite3.Connection, workspace_id: int
) -> Optional[LicenseKey]:
    """Latest active licence_key for a workspace, or None if revoked.

    Used by /v2/licence/validate and /v2/usage/emit to enforce revocation
    within the 24h cache bound — without this lookup, a stolen JWT would
    remain bearer-of-trust until its 365-day ``exp`` claim.
    """
    row = conn.execute(
        """SELECT * FROM license_keys
           WHERE workspace_id = ? AND is_active = 1
           ORDER BY created_at DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    return row_to_license_key(row) if row else None


def _get_license_for_user(
    conn: sqlite3.Connection, user_id: int, *, include_inactive: bool
) -> Optional[LicenseKey]:
    """Shared query for the user's most recent licence row.

    The only difference between the active-only and unfiltered reads is the
    ``is_active = 1`` predicate, so both public accessors delegate here with the
    flag toggled rather than duplicating the SELECT (DRY).

    Args:
        include_inactive: When False (default for ``get_license_for_user``),
            restrict to ``is_active = 1`` rows — lapsed/revoked rows are hidden.
            When True, return the most recent row regardless of ``is_active``,
            so the Lapsed state (REVUE-382) is reachable.
    """
    active_filter = "" if include_inactive else " AND lk.is_active = 1"
    # REVUE-413 updates the licence row IN PLACE (one row per user via
    # set_license_subscription_state / update_user_tier), so a user normally has
    # a single licence row. The ORDER BY is defensive against historical/multi-row
    # data: ``is_active DESC`` makes an active row win over a lapsed one before
    # falling back to most-recent, so the unfiltered read never prefers a stale
    # lapsed row when an active one also exists.
    row = conn.execute(
        f"""SELECT lk.* FROM license_keys lk
           JOIN workspaces w ON lk.workspace_id = w.id
           WHERE w.user_id = ?{active_filter}
           ORDER BY lk.is_active DESC, lk.created_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    return row_to_license_key(row) if row else None


def get_license_for_user(conn: sqlite3.Connection, user_id: int) -> Optional[LicenseKey]:
    return _get_license_for_user(conn, user_id, include_inactive=False)


def get_any_license_for_user(conn: sqlite3.Connection, user_id: int) -> Optional[LicenseKey]:
    """Return the most recent licence row for a user WITHOUT filtering on is_active.

    REVUE-382: the Account→Plan page needs to see lapsed rows (is_active=0,
    tier preserved) to render the Lapsed state. ``get_license_for_user`` hides
    them intentionally for all other surfaces (dashboard, billing, validation).
    Do NOT use this function on those paths.
    """
    return _get_license_for_user(conn, user_id, include_inactive=True)


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


def touch_license_validated(conn: sqlite3.Connection, license_key_id: int) -> None:
    """Stamp ``last_validated_at = now`` (UTC, naive isoformat) on a licence row.

    REVUE-382: called on each SUCCESSFUL ``/v2/licence/validate`` so the Account
    → Plan page can render "Last verified Nh ago" and distinguish the
    not-activated state (never validated) from the active state. The naive-UTC
    isoformat matches ``period_reset_at`` so all licence timestamps share one
    representation (no tz-aware/naive split when diffing).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    conn.execute(
        "UPDATE license_keys SET last_validated_at = ? WHERE id = ?",
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


def has_recent_track_event(
    conn: sqlite3.Connection,
    license_key_id: int,
    repo_id: Optional[str],
    window_seconds: int = 60,
) -> bool:
    """Return True if a review_run for this key+repo exists within the last N seconds.

    Empty repo_id normalised to None by callers — consistent with create_review_run.
    Used by POST /usage/track to deduplicate CLI retries (AC5).
    """
    row = conn.execute(
        """SELECT 1 FROM review_runs
           WHERE license_key_id = ?
             AND (repo_id = ? OR (repo_id IS NULL AND ? IS NULL))
             AND created_at > datetime('now', ?)
           LIMIT 1""",
        (license_key_id, repo_id, repo_id, f"-{window_seconds} seconds"),
    ).fetchone()
    return row is not None


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


def get_conversion_analytics(
    conn: sqlite3.Connection,
    days: int = 30,
) -> dict:
    """Return admin-level conversion analytics across ALL users.

    Returns:
        {
          "tier_breakdown":        {"free": N, "indie": N, ...},
          "reviews_per_month_buckets": {"0": N, "1-5": N, "6-25": N, "26-100": N, "100+": N},
          "referral_sources":      [{"source": str, "count": N}],
          "total_users":           N,
          "paid_users":            N,
          "conversion_rate":       float,
          "signups_over_time":     [{"date": "YYYY-MM-DD", "count": N}],
        }
    """
    # --- Tier breakdown ---
    tier_rows = conn.execute(
        "SELECT tier, COUNT(*) as cnt FROM users GROUP BY tier"
    ).fetchall()
    tier_breakdown = {
        "free": 0, "indie": 0, "pro": 0,
        "enterprise_starter": 0, "enterprise_growth": 0, "enterprise_plus": 0,
    }
    for row in tier_rows:
        tier_breakdown[row["tier"]] = row["cnt"]

    total_users = sum(tier_breakdown.values())
    paid_users = total_users - tier_breakdown.get("free", 0)
    conversion_rate = round((paid_users / total_users) * 100, 1) if total_users > 0 else 0.0

    # --- Reviews per month buckets ---
    # Count reviews per user in the last 30 days, then bucket
    bucket_rows = conn.execute(
        """SELECT u.id,
                  COALESCE(cnt.review_count, 0) as review_count
           FROM users u
           LEFT JOIN (
               SELECT w.user_id, COUNT(*) as review_count
               FROM review_runs rr
               JOIN license_keys lk ON rr.license_key_id = lk.id
               JOIN workspaces w ON lk.workspace_id = w.id
               WHERE rr.created_at >= datetime('now', '-30 days')
               GROUP BY w.user_id
           ) cnt ON u.id = cnt.user_id"""
    ).fetchall()
    buckets = {"0": 0, "1-5": 0, "6-25": 0, "26-100": 0, "100+": 0}
    for row in bucket_rows:
        c = row["review_count"]
        if c == 0:
            buckets["0"] += 1
        elif c <= 5:
            buckets["1-5"] += 1
        elif c <= 25:
            buckets["6-25"] += 1
        elif c <= 100:
            buckets["26-100"] += 1
        else:
            buckets["100+"] += 1

    # --- Referral sources ---
    ref_rows = conn.execute(
        """SELECT COALESCE(referral_source, 'direct') as source, COUNT(*) as cnt
           FROM users GROUP BY source ORDER BY cnt DESC"""
    ).fetchall()
    referral_sources = [{"source": row["source"], "count": row["cnt"]} for row in ref_rows]

    # --- Signups over time ---
    signup_rows = conn.execute(
        """SELECT DATE(created_at) as dt, COUNT(*) as cnt
           FROM users
           WHERE created_at >= datetime('now', ?)
           GROUP BY dt ORDER BY dt ASC""",
        (f"-{days} days",),
    ).fetchall()
    signups_over_time = [{"date": row["dt"], "count": row["cnt"]} for row in signup_rows]

    return {
        "tier_breakdown": tier_breakdown,
        "reviews_per_month_buckets": buckets,
        "referral_sources": referral_sources,
        "total_users": total_users,
        "paid_users": paid_users,
        "conversion_rate": conversion_rate,
        "signups_over_time": signups_over_time,
    }


# --- Usage event queries (REVUE-278) ---

def record_usage_event(
    conn: sqlite3.Connection,
    *,
    workspace_id: int,
    reviews_run: int,
    findings_count: int,
    emitted_at: int,
) -> int:
    """Insert one per-invocation usage record. ``received_at`` is set by
    SQLite's ``CURRENT_TIMESTAMP`` default — never accept it from the
    client. The client-supplied ``emitted_at`` is stored for skew
    diagnostics but never trusted for billing windows.
    """
    cur = conn.execute(
        """INSERT INTO usage_events
           (workspace_id, reviews_run, findings_count, emitted_at)
           VALUES (?, ?, ?, ?)""",
        (workspace_id, reviews_run, findings_count, emitted_at),
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_usage_events_for_workspace(
    conn: sqlite3.Connection,
    workspace_id: int,
    limit: int = 1000,
) -> list[UsageEvent]:
    """Return usage events for one workspace, newest first. The composite
    index ``idx_usage_events_workspace_received_at`` covers this query."""
    rows = conn.execute(
        """SELECT id, workspace_id, reviews_run, findings_count,
                  emitted_at, received_at
           FROM usage_events
           WHERE workspace_id = ?
           ORDER BY received_at DESC LIMIT ?""",
        (workspace_id, limit),
    ).fetchall()
    return [
        UsageEvent(
            id=r["id"],
            workspace_id=r["workspace_id"],
            reviews_run=r["reviews_run"],
            findings_count=r["findings_count"],
            emitted_at=r["emitted_at"],
            received_at=str(r["received_at"]),
        )
        for r in rows
    ]


def count_usage_events_since_month_start(
    conn: sqlite3.Connection,
    workspace_id: int,
) -> int:
    """Count usage events received this UTC calendar month.

    The ``received_at`` timestamp is server-issued (not client-supplied),
    so the count naturally resets at 00:00 UTC on the 1st without a
    scheduled job. Used by the free-tier paywall to enforce the monthly
    review cap (REVUE-279 AC1).

    Implementation note: the lex string ``>=`` compare against ``received_at``
    is only safe because both sides use the canonical
    ``USAGE_RECEIVED_AT_FORMAT`` (``YYYY-MM-DD HH:MM:SS``), enforced at write
    time by the schema CHECK constraint in ``database.py``. The format is
    fixed-width and zero-padded, so lexicographic ordering matches
    chronological ordering. Any future writer using ISO-8601 with ``T``
    separator, date-only values, or epoch strings would silently break this —
    that's why the format lives as a single shared constant.
    """
    from database import USAGE_RECEIVED_AT_FORMAT
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_iso = month_start.strftime(USAGE_RECEIVED_AT_FORMAT)

    row = conn.execute(
        """SELECT COUNT(*) as cnt
           FROM usage_events
           WHERE workspace_id = ? AND received_at >= ?""",
        (workspace_id, month_start_iso),
    ).fetchone()

    return row["cnt"] if row else 0


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


def set_license_subscription_state(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    is_active: bool,
    subscription_status: Optional[str] = None,
    current_period_end: Optional[str] = None,
) -> None:
    """Persist Stripe subscription state onto the user's licence row(s) WITHOUT
    touching tier or reviews_limit.

    REVUE-413: this is the lapsed/recovery path. ``update_user_tier`` force-sets
    tier + reviews_limit, which would discard the retained tier the Lapsed state
    depends on, so the lapsed/active-recovery transitions use this function
    instead. ``is_active=False`` with the tier left intact is what makes the
    Lapsed state (inactive licence, tier preserved) reachable in production;
    flipping ``is_active`` back to True on recovery un-locks a user who paid up.

    Scope matches ``update_user_tier``: every licence under the user's workspaces.
    """
    conn.execute(
        """UPDATE license_keys
           SET is_active = ?, subscription_status = ?, current_period_end = ?
           WHERE workspace_id IN (SELECT id FROM workspaces WHERE user_id = ?)""",
        (1 if is_active else 0, subscription_status, current_period_end, user_id),
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

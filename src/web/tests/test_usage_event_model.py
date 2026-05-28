"""REVUE-278 Task 1 — UsageEvent model + schema.

Covers the persistence contract used by the ``POST /api/v2/usage/emit``
endpoint (Task 2). The model is a thin record of one per-invocation
``/revue-local`` run; the index on ``(workspace_id, received_at)`` is
load-bearing for the cohort/usage queries planned in REVUE-279.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def test_record_usage_event_persists_row_with_expected_columns(_tmp_db):
    """A recorded event round-trips through the DB with every field
    preserved — the model is a flat record, no JSON-encoded blobs."""
    # Arrange
    from database import get_db
    from models import (
        create_user,
        create_workspace,
        record_usage_event,
        get_usage_events_for_workspace,
    )
    with get_db() as conn:
        uid = create_user(conn, "usage-a@test.com", "h")
        wsid = create_workspace(conn, uid, "ws-usage-a")

    # Act
    with get_db() as conn:
        event_id = record_usage_event(
            conn,
            workspace_id=wsid,
            reviews_run=3,
            findings_count=11,
            emitted_at=1_750_000_000,  # epoch seconds, client-supplied
        )

    # Assert — row created and round-trips with all client-supplied fields
    assert isinstance(event_id, int) and event_id > 0
    with get_db() as conn:
        events = get_usage_events_for_workspace(conn, wsid)
    assert len(events) == 1
    ev = events[0]
    assert ev.workspace_id == wsid
    assert ev.reviews_run == 3
    assert ev.findings_count == 11
    assert ev.emitted_at == 1_750_000_000


def test_record_usage_event_sets_received_at_server_side(_tmp_db):
    """``received_at`` is server-issued (NOT trusted from the client) so
    cohort analysis can rely on a monotonic, server-controlled timeline
    even if a client clock is wildly skewed."""
    # Arrange
    from database import get_db
    from models import (
        create_user,
        create_workspace,
        record_usage_event,
        get_usage_events_for_workspace,
    )
    with get_db() as conn:
        uid = create_user(conn, "usage-b@test.com", "h")
        wsid = create_workspace(conn, uid, "ws-usage-b")

    before = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    # Act — emitted_at is in the far past; received_at must be NOW
    with get_db() as conn:
        record_usage_event(
            conn,
            workspace_id=wsid,
            reviews_run=1,
            findings_count=0,
            emitted_at=0,  # 1970 — proves received_at is not just emitted_at
        )

    after = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    # Add 1 second to after to account for SQLite timestamp granularity
    after = after.replace(second=after.second + 1) if after.second < 59 else after

    # Assert — received_at falls inside the wall-clock window we observed
    # (SQLite CURRENT_TIMESTAMP has second granularity, not microsecond)
    with get_db() as conn:
        events = get_usage_events_for_workspace(conn, wsid)
    assert len(events) == 1
    received_at = datetime.fromisoformat(events[0].received_at)
    assert before <= received_at <= after, (
        f"received_at={received_at!r} should be between {before!r} and "
        f"{after!r} — server-side clock, not client-supplied"
    )


def _seed_workspace(email: str, name: str) -> int:
    """Create a user + workspace; return workspace_id. Helper for the
    REVUE-279 counter tests below — keeps the AAA structure terse."""
    from database import get_db
    from models import create_user, create_workspace
    with get_db() as conn:
        uid = create_user(conn, email, "h")
        return create_workspace(conn, uid, name)


def _seed_event_at(workspace_id: int, received_at_iso: str) -> None:
    """Insert one usage_events row with an explicit ``received_at`` so the
    REVUE-279 counter can be tested against month-boundary edge cases.

    The production path (``record_usage_event``) lets SQLite stamp
    ``received_at`` via ``CURRENT_TIMESTAMP``; this helper bypasses that to
    place rows on either side of the UTC month boundary."""
    from database import get_db
    with get_db() as conn:
        conn.execute(
            """INSERT INTO usage_events
               (workspace_id, reviews_run, findings_count, emitted_at, received_at)
               VALUES (?, 1, 0, 0, ?)""",
            (workspace_id, received_at_iso),
        )


# --- REVUE-279 Task 1: count_usage_events_since_month_start -------------------

def test_count_usage_events_since_month_start_returns_in_month_count(_tmp_db):
    """REVUE-279 AC1 — the free-tier counter sums events received this
    UTC calendar month for the given workspace. Events seeded with
    ``received_at`` before the UTC month boundary are excluded so the
    counter resets at 00:00 UTC on the 1st without a scheduled job
    (AC2). The window is computed at query time."""
    # Arrange — three events this month, two events last month
    from models import count_usage_events_since_month_start
    from database import get_db
    wsid = _seed_workspace("counter-a@test.com", "ws-counter-a")
    now = datetime.now(timezone.utc)
    last_month = (now.replace(day=1) - timedelta(days=1)).replace(
        hour=23, minute=59, second=0, microsecond=0
    )
    this_month_start = now.replace(
        day=1, hour=0, minute=0, second=1, microsecond=0
    )
    _seed_event_at(wsid, last_month.strftime("%Y-%m-%d %H:%M:%S"))
    _seed_event_at(wsid, last_month.strftime("%Y-%m-%d %H:%M:%S"))
    _seed_event_at(wsid, this_month_start.strftime("%Y-%m-%d %H:%M:%S"))
    _seed_event_at(wsid, this_month_start.strftime("%Y-%m-%d %H:%M:%S"))
    _seed_event_at(wsid, this_month_start.strftime("%Y-%m-%d %H:%M:%S"))

    # Act
    with get_db() as conn:
        count = count_usage_events_since_month_start(conn, wsid)

    # Assert — only the three in-month events count
    assert count == 3


def test_count_usage_events_excludes_other_workspaces(_tmp_db):
    """The counter is per-workspace; events for sibling workspaces must
    never bleed into the count. Tenancy isolation defence."""
    # Arrange — one event each for two workspaces
    from models import count_usage_events_since_month_start, record_usage_event
    from database import get_db
    wsid_a = _seed_workspace("counter-b@test.com", "ws-counter-b")
    wsid_b = _seed_workspace("counter-c@test.com", "ws-counter-c")
    with get_db() as conn:
        record_usage_event(
            conn, workspace_id=wsid_a, reviews_run=1, findings_count=0, emitted_at=0
        )
        record_usage_event(
            conn, workspace_id=wsid_b, reviews_run=1, findings_count=0, emitted_at=0
        )

    # Act
    with get_db() as conn:
        count_a = count_usage_events_since_month_start(conn, wsid_a)
        count_b = count_usage_events_since_month_start(conn, wsid_b)

    # Assert — each workspace sees its own single event
    assert count_a == 1
    assert count_b == 1


def test_count_usage_events_returns_zero_for_unknown_workspace(_tmp_db):
    """Querying a workspace_id that has never emitted returns 0, not
    None or an error. The /validate handler relies on this to treat
    fresh workspaces as having 0 reviews used."""
    # Arrange — nothing seeded
    from models import count_usage_events_since_month_start
    from database import get_db

    # Act
    with get_db() as conn:
        count = count_usage_events_since_month_start(conn, 999_999)

    # Assert
    assert count == 0


def test_usage_events_index_on_workspace_id_received_at_exists(_tmp_db):
    """The ``(workspace_id, received_at)`` index is load-bearing — the
    REVUE-279 free-tier paywall query scans by this composite. Without
    the index the query falls back to a full table scan; this test pins
    the index name into the schema contract."""
    # Arrange
    from database import get_db

    # Act
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'usage_events'"
        ).fetchall()

    # Assert — the composite index is present and named per the migration
    index_names = {r["name"] for r in rows}
    assert "idx_usage_events_workspace_received_at" in index_names, (
        f"expected composite index on usage_events; got {index_names!r}"
    )


# --- REVUE-279 PLAUSIBLE fix: CHECK constraint on received_at format ---

def test_usage_events_received_at_format_check_rejects_iso8601_t_separator(_tmp_db):
    """REVUE-279 code-review defence-in-depth: the count_usage_events
    paywall query uses lex string comparison; if a future writer inserts
    received_at as ISO-8601 with 'T' separator the schema CHECK must
    reject it at write time, not silently let it skew the counter."""
    import sqlite3
    from database import get_db
    wsid = _seed_workspace("checkfmt-a@test.com", "ws-checkfmt-a")

    with get_db() as conn:
        # ISO-8601 with T separator — rejected by GLOB pattern
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO usage_events
                   (workspace_id, reviews_run, findings_count, emitted_at, received_at)
                   VALUES (?, 1, 0, 0, '2026-05-28T12:34:56')""",
                (wsid,),
            )


def test_usage_events_received_at_format_check_rejects_date_only(_tmp_db):
    """A date-only value (no time component) sorts BEFORE 'YYYY-MM-DD 00:00:00'
    in lex order — would silently exclude month-boundary events from the
    counter. Schema CHECK must reject it."""
    import sqlite3
    from database import get_db
    wsid = _seed_workspace("checkfmt-b@test.com", "ws-checkfmt-b")

    with get_db() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO usage_events
                   (workspace_id, reviews_run, findings_count, emitted_at, received_at)
                   VALUES (?, 1, 0, 0, '2026-05-28')""",
                (wsid,),
            )


def test_usage_events_received_at_format_check_rejects_integer_epoch(_tmp_db):
    """An integer epoch string ('1717000000') compares LESS than
    'YYYY-MM-DD 00:00:00' lexically — silently excluded from the counter.
    Schema CHECK must reject it."""
    import sqlite3
    from database import get_db
    wsid = _seed_workspace("checkfmt-c@test.com", "ws-checkfmt-c")

    with get_db() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO usage_events
                   (workspace_id, reviews_run, findings_count, emitted_at, received_at)
                   VALUES (?, 1, 0, 0, '1717000000')""",
                (wsid,),
            )


def test_usage_events_received_at_format_check_accepts_canonical_format(_tmp_db):
    """The canonical 'YYYY-MM-DD HH:MM:SS' format (matching SQLite's
    CURRENT_TIMESTAMP output and the strftime in count_usage_events_since_month_start)
    must still be accepted — the CHECK is a guard, not a wall."""
    from database import get_db
    wsid = _seed_workspace("checkfmt-d@test.com", "ws-checkfmt-d")

    with get_db() as conn:
        # No exception
        conn.execute(
            """INSERT INTO usage_events
               (workspace_id, reviews_run, findings_count, emitted_at, received_at)
               VALUES (?, 1, 0, 0, '2026-05-28 12:34:56')""",
            (wsid,),
        )
        cnt = conn.execute(
            "SELECT COUNT(*) as c FROM usage_events WHERE workspace_id = ?",
            (wsid,),
        ).fetchone()["c"]
        assert cnt == 1

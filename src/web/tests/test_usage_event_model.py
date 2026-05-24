"""REVUE-278 Task 1 — UsageEvent model + schema.

Covers the persistence contract used by the ``POST /api/v2/usage/emit``
endpoint (Task 2). The model is a thin record of one per-invocation
``/revue-local`` run; the index on ``(workspace_id, received_at)`` is
load-bearing for the cohort/usage queries planned in REVUE-279.
"""
from __future__ import annotations

from datetime import datetime, timezone


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

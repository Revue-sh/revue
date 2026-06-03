"""
Integration tests for src/db/auto_scorer.py — require a running Postgres DB.

All tests are marked @pytest.mark.integration and skipped when DATABASE_URL
is not set.
"""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")  # skip entire module if psycopg2 not installed

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DB not available",
    ),
]

from psycopg2.extras import RealDictCursor

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from db.auto_scorer import score_finding, score_findings
from db.import_review import (
    get_lookup_id,
    get_or_create_model,
    import_comparison,
    import_review,
)


@pytest.fixture(scope="module")
def db_connection():
    """Database connection for all integration tests."""
    database_url = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
    yield conn
    conn.close()


def _insert_test_finding(cursor, ticket_suffix: str) -> int:
    """Helper: insert a review + finding, return the finding_id."""
    ticket_id = f"REVUE-SCORER-{ticket_suffix}-{int(time.time())}"
    model_id = get_or_create_model(cursor, "test-scorer-model", "anthropic")
    tier_id = get_lookup_id(cursor, "tiers", "free")
    mode_id = get_lookup_id(cursor, "review_modes", "baseline")

    cursor.execute(
        """
        INSERT INTO reviews (ticket_id, branch, model_id, tier_id, mode_id, total_findings)
        VALUES (%s, 'test', %s, %s, %s, 1)
        RETURNING id
        """,
        (ticket_id, model_id, tier_id, mode_id),
    )
    review_id = cursor.fetchone()["id"]

    severity_id = get_lookup_id(cursor, "severity_levels", "info")
    cursor.execute(
        """
        INSERT INTO findings (review_id, file_path, severity_id, category, issue, details, recommendation)
        VALUES (%s, 'test.py', %s, 'test', 'Test issue text here', 'Some details', 'Add a fix')
        RETURNING id
        """,
        (review_id, severity_id),
    )
    return cursor.fetchone()["id"]


# ---------------------------------------------------------------------------
# AC1 — scorer inserts two rows per finding
# ---------------------------------------------------------------------------

def test_scorer_inserts_two_rows_per_finding(db_connection):
    cursor = db_connection.cursor()
    finding_id = _insert_test_finding(cursor, "2rows")

    finding_dict = {
        "issue": "Test issue text here",
        "details": "Some details",
        "recommendation": "Add a fix",
    }
    score_finding(cursor, finding_id, finding_dict)

    cursor.execute(
        "SELECT * FROM finding_quality WHERE finding_id = %s",
        (finding_id,),
    )
    rows = cursor.fetchall()
    assert len(rows) == 2

    # Verify dimensions
    dim_ids = {r["dimension_id"] for r in rows}
    cursor.execute("SELECT id FROM quality_dimensions WHERE name = 'clarity'")
    clarity_id = cursor.fetchone()["id"]
    cursor.execute("SELECT id FROM quality_dimensions WHERE name = 'actionability'")
    action_id = cursor.fetchone()["id"]
    assert dim_ids == {clarity_id, action_id}

    # Verify rated_by = auto
    cursor.execute("SELECT id FROM rating_sources WHERE name = 'auto'")
    auto_id = cursor.fetchone()["id"]
    assert all(r["rated_by_id"] == auto_id for r in rows)

    db_connection.rollback()


# ---------------------------------------------------------------------------
# AC5 — idempotent inserts
# ---------------------------------------------------------------------------

def test_idempotent_inserts(db_connection):
    cursor = db_connection.cursor()
    finding_id = _insert_test_finding(cursor, "idempotent")

    finding_dict = {
        "issue": "Duplicate test issue",
        "details": "Details here",
        "recommendation": "Remove the duplication",
    }

    # Score twice
    score_finding(cursor, finding_id, finding_dict)
    score_finding(cursor, finding_id, finding_dict)

    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM finding_quality WHERE finding_id = %s",
        (finding_id,),
    )
    assert cursor.fetchone()["cnt"] == 2  # one per dimension, not four

    db_connection.rollback()


# ---------------------------------------------------------------------------
# AC3 — human override read convention
# ---------------------------------------------------------------------------

def test_human_override_read_convention(db_connection):
    cursor = db_connection.cursor()
    finding_id = _insert_test_finding(cursor, "override")

    # Look up IDs
    cursor.execute("SELECT id FROM quality_dimensions WHERE name = 'clarity'")
    clarity_id = cursor.fetchone()["id"]
    cursor.execute("SELECT id FROM rating_sources WHERE name = 'auto'")
    auto_id = cursor.fetchone()["id"]
    cursor.execute("SELECT id FROM rating_sources WHERE name = 'human'")
    human_id = cursor.fetchone()["id"]

    # Insert auto score (score=2)
    cursor.execute(
        """
        INSERT INTO finding_quality (finding_id, dimension_id, score, rated_by_id)
        VALUES (%s, %s, 2, %s)
        ON CONFLICT (finding_id, dimension_id, rated_by_id) DO NOTHING
        """,
        (finding_id, clarity_id, auto_id),
    )

    # Insert human score (score=4)
    cursor.execute(
        """
        INSERT INTO finding_quality (finding_id, dimension_id, score, rated_by_id)
        VALUES (%s, %s, 4, %s)
        ON CONFLICT (finding_id, dimension_id, rated_by_id) DO NOTHING
        """,
        (finding_id, clarity_id, human_id),
    )

    # Read-time convention: human rating should win
    cursor.execute(
        """
        SELECT DISTINCT ON (fq.finding_id, fq.dimension_id)
            fq.score
        FROM finding_quality fq
        JOIN rating_sources rs ON rs.id = fq.rated_by_id
        WHERE fq.finding_id = %s AND fq.dimension_id = %s
        ORDER BY fq.finding_id, fq.dimension_id,
                 CASE rs.name WHEN 'human' THEN 0 ELSE 1 END,
                 fq.rated_at DESC
        """,
        (finding_id, clarity_id),
    )
    row = cursor.fetchone()
    assert row["score"] == 4  # human score, not auto score of 2

    db_connection.rollback()


# ---------------------------------------------------------------------------
# AC1 — integration with import_comparison end-to-end
# ---------------------------------------------------------------------------

def test_integration_with_import_comparison(db_connection):
    with tempfile.TemporaryDirectory() as tmpdir:
        comp_dir = Path(tmpdir) / f"REVUE-SCORER-E2E-{int(time.time())}"
        comp_dir.mkdir()

        findings_data = [
            {
                "severity": "high",
                "issue": "Security vulnerability in src/auth.py login handler",
                "file_path": "src/auth.py",
                "category": "security",
                "details": "Password not hashed before storage",
                "recommendation": "Add bcrypt hashing in src/auth.py",
            }
        ]
        (comp_dir / "baseline.json").write_text(
            json.dumps([{"review": json.dumps(findings_data)}])
        )
        (comp_dir / "pr_description.txt").write_text("## Summary\nTest PR")

        import_comparison(
            comp_dir,
            model="test-scorer-e2e",
            provider="anthropic",
            branch="test-branch",
            tier="free",
        )

        # Verify finding_quality rows exist for the imported finding
        ticket_id = comp_dir.name
        cursor = db_connection.cursor()

        cursor.execute(
            """
            SELECT fq.*
            FROM finding_quality fq
            JOIN findings f ON f.id = fq.finding_id
            JOIN reviews r ON r.id = f.review_id
            JOIN rating_sources rs ON rs.id = fq.rated_by_id
            WHERE r.ticket_id = %s AND rs.name = 'auto'
            """,
            (ticket_id,),
        )
        rows = cursor.fetchall()
        # One finding → two quality rows (clarity + actionability)
        assert len(rows) == 2

        # Scores should be within valid range
        for row in rows:
            assert 1 <= row["score"] <= 5

        db_connection.rollback()

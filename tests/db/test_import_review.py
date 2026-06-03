"""
Tests for src/db/import_review.py

Test Cases:
- test_load_findings_list_format: Parse list-based JSON
- test_load_findings_dict_format: Parse dict-based JSON
- test_import_review_creates_review_and_findings: Insert review + findings
- test_import_review_idempotent: Re-running same import doesn't duplicate
- test_import_pr_description: Parse and insert PR description sections
- test_import_pr_description_deduplication: SHA256 hash prevents duplicates
- test_import_comparison_links_baseline_contextual: comparison_runs junction
- test_graceful_degradation_db_unreachable: Warning printed, exit 0
"""
import json
import os
import sys
import pathlib
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

psycopg2 = pytest.importorskip("psycopg2")  # skip entire module if psycopg2 not installed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from db.import_review import (
    load_findings,
    import_review,
    import_pr_description,
    import_comparison,
    get_or_create_model,
    get_lookup_id,
    _parse_pr_sections
)


@pytest.fixture(scope="module")
def db_connection():
    """Database connection for all tests."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        pytest.skip("DATABASE_URL not set")
    
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
    yield conn
    conn.close()


@pytest.fixture
def temp_comparison_dir():
    """Create temporary comparison directory with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        comp_dir = Path(tmpdir) / "REVUE-TEST"
        comp_dir.mkdir()
        yield comp_dir


# ---------------------------------------------------------------------------
# JSON Parsing Tests
# ---------------------------------------------------------------------------

def test_load_findings_list_format(temp_comparison_dir):
    """Test parsing list-based JSON format."""
    json_path = temp_comparison_dir / "test.json"
    json_path.write_text(json.dumps([
        {
            "review": json.dumps([
                {"severity": "high", "issue": "SQL injection", "file_path": "db.py"},
                {"severity": "medium", "issue": "Missing validation", "file_path": "api.py"}
            ])
        }
    ]))
    
    findings = load_findings(json_path)
    assert len(findings) == 2
    assert findings[0]["severity"] == "high"
    assert findings[0]["issue"] == "SQL injection"
    assert findings[1]["file_path"] == "api.py"


def test_load_findings_dict_format(temp_comparison_dir):
    """Test parsing dict-based JSON format."""
    json_path = temp_comparison_dir / "test.json"
    json_path.write_text(json.dumps({
        "results": [
            {
                "review": json.dumps({
                    "findings": [
                        {"severity": "critical", "issue": "XSS vulnerability", "file_path": "web.py"}
                    ]
                })
            }
        ]
    }))
    
    findings = load_findings(json_path)
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"


def test_parse_pr_sections():
    """Test PR description section parsing."""
    content = """
# PR Title

## Summary
This is a summary.

## Out of Scope
Not included in this PR.

## Technical Details
Some implementation notes.
"""
    sections = _parse_pr_sections(content)
    assert "summary" in sections
    assert "out_of_scope" in sections
    assert "This is a summary" in sections["summary"]
    assert "Not included" in sections["out_of_scope"]


# ---------------------------------------------------------------------------
# Database Import Tests
# ---------------------------------------------------------------------------

def test_get_or_create_model(db_connection):
    """Test model lookup/insert."""
    cursor = db_connection.cursor()
    
    # First call: creates model
    model_id_1 = get_or_create_model(cursor, "test-model-1", "anthropic")
    assert model_id_1 is not None
    
    # Second call: returns existing
    model_id_2 = get_or_create_model(cursor, "test-model-1", "anthropic")
    assert model_id_1 == model_id_2
    
    db_connection.rollback()  # Don't persist test data


def test_get_lookup_id(db_connection):
    """Test reference table lookup."""
    cursor = db_connection.cursor()
    
    # Should find existing severity levels
    critical_id = get_lookup_id(cursor, "severity_levels", "critical")
    high_id = get_lookup_id(cursor, "severity_levels", "high")
    assert critical_id != high_id
    
    # Should raise on unknown value
    with pytest.raises(ValueError, match="Unknown severity_levels value"):
        get_lookup_id(cursor, "severity_levels", "unknown_severity")


def test_import_review_creates_review_and_findings(db_connection, temp_comparison_dir):
    """AC1: Findings written with correct mode_id."""
    cursor = db_connection.cursor()
    
    # Use unique ticket ID to avoid conflicts
    import time
    ticket_id = f"REVUE-TEST-{int(time.time())}"
    
    # Create test JSON
    json_path = temp_comparison_dir / "baseline.json"
    json_path.write_text(json.dumps([
        {
            "review": json.dumps([
                {
                    "severity": "high",
                    "issue": "Test issue",
                    "file_path": "test.py",
                    "category": "security",
                    "details": "Test details",
                    "recommendation": "Fix it"
                }
            ])
        }
    ]))
    
    # Get reference IDs
    model_id = get_or_create_model(cursor, "test-model", "anthropic")
    tier_id = get_lookup_id(cursor, "tiers", "free")
    mode_id = get_lookup_id(cursor, "review_modes", "baseline")
    
    # Import review
    review_id, _ = import_review(
        cursor, json_path, ticket_id, "test-branch",
        model_id, tier_id, mode_id
    )

    assert review_id is not None
    
    # Verify review row
    cursor.execute("SELECT * FROM reviews WHERE id = %s", (review_id,))
    review = cursor.fetchone()
    assert review['ticket_id'] == ticket_id
    assert review['total_findings'] == 1
    assert review['mode_id'] == mode_id
    
    # Verify findings row
    cursor.execute("SELECT * FROM findings WHERE review_id = %s", (review_id,))
    findings = cursor.fetchall()
    assert len(findings) == 1
    assert findings[0]['issue'] == "Test issue"
    assert findings[0]['category'] == "security"
    
    db_connection.rollback()


def test_import_review_idempotent(db_connection, temp_comparison_dir):
    """AC5: Re-running same import doesn't duplicate rows."""
    cursor = db_connection.cursor()
    
    json_path = temp_comparison_dir / "baseline.json"
    json_path.write_text(json.dumps([
        {"review": json.dumps([{"severity": "info", "issue": "Test", "file_path": "x.py"}])}
    ]))
    
    model_id = get_or_create_model(cursor, "test-model-2", "anthropic")
    tier_id = get_lookup_id(cursor, "tiers", "free")
    mode_id = get_lookup_id(cursor, "review_modes", "baseline")
    
    # First import
    review_id_1, _ = import_review(
        cursor, json_path, "REVUE-TEST-2", "test-branch",
        model_id, tier_id, mode_id
    )

    # Second import (should return existing ID)
    review_id_2, _ = import_review(
        cursor, json_path, "REVUE-TEST-2", "test-branch",
        model_id, tier_id, mode_id
    )

    assert review_id_1 == review_id_2
    
    # Verify only one review exists
    cursor.execute(
        "SELECT COUNT(*) as count FROM reviews WHERE ticket_id = %s",
        ("REVUE-TEST-2",)
    )
    count = cursor.fetchone()['count']
    assert count == 1
    
    db_connection.rollback()


def test_import_pr_description(db_connection, temp_comparison_dir):
    """AC2: PR description parsed into sections."""
    cursor = db_connection.cursor()
    
    pr_desc_path = temp_comparison_dir / "pr_description.txt"
    pr_desc_path.write_text("""
## Summary
This is a test PR.

## Out of Scope
Not doing this.
""")
    
    pr_desc_id = import_pr_description(cursor, pr_desc_path, "REVUE-TEST-3")
    assert pr_desc_id is not None
    
    # Verify sections
    cursor.execute(
        "SELECT section_type, content FROM pr_description_sections WHERE pr_description_id = %s",
        (pr_desc_id,)
    )
    sections = {row['section_type']: row['content'] for row in cursor.fetchall()}
    assert "summary" in sections
    assert "out_of_scope" in sections
    assert "This is a test PR" in sections["summary"]
    
    db_connection.rollback()


def test_import_pr_description_deduplication(db_connection, temp_comparison_dir):
    """Test SHA256 hash prevents duplicate PR descriptions."""
    cursor = db_connection.cursor()
    
    pr_desc_path = temp_comparison_dir / "pr_description.txt"
    pr_desc_path.write_text("## Summary\nSame content")
    
    # First import
    pr_desc_id_1 = import_pr_description(cursor, pr_desc_path, "REVUE-TEST-4")
    
    # Second import (same content)
    pr_desc_id_2 = import_pr_description(cursor, pr_desc_path, "REVUE-TEST-4")
    
    assert pr_desc_id_1 == pr_desc_id_2
    
    # Verify only one row
    cursor.execute("SELECT COUNT(*) as count FROM pr_descriptions WHERE ticket_id = %s", ("REVUE-TEST-4",))
    count = cursor.fetchone()['count']
    assert count == 1
    
    db_connection.rollback()


def test_import_comparison_links_baseline_contextual(db_connection, temp_comparison_dir):
    """AC3: comparison_runs row links baseline and contextual."""
    cursor = db_connection.cursor()
    
    # Create test files
    (temp_comparison_dir / "baseline.json").write_text(json.dumps([
        {"review": json.dumps([{"severity": "info", "issue": "Test", "file_path": "x.py"}])}
    ]))
    (temp_comparison_dir / "contextual.json").write_text(json.dumps([
        {"review": json.dumps([{"severity": "info", "issue": "Test 2", "file_path": "y.py"}])}
    ]))
    (temp_comparison_dir / "pr_description.txt").write_text("## Summary\nTest PR")
    
    # Import comparison
    import_comparison(
        temp_comparison_dir,
        model="test-model-3",
        provider="anthropic",
        branch="test-branch",
        tier="free"
    )
    
    # Verify comparison_runs exists
    ticket_id = temp_comparison_dir.name
    cursor.execute(
        """
        SELECT cr.id, r1.mode_id AS baseline_mode, r2.mode_id AS contextual_mode
        FROM comparison_runs cr
        JOIN reviews r1 ON cr.baseline_review_id = r1.id
        JOIN reviews r2 ON cr.contextual_review_id = r2.id
        WHERE r1.ticket_id = %s
        """,
        (ticket_id,)
    )
    
    row = cursor.fetchone()
    assert row is not None
    
    # Verify modes are correct
    baseline_mode_id = get_lookup_id(cursor, "review_modes", "baseline")
    contextual_mode_id = get_lookup_id(cursor, "review_modes", "contextual")
    assert row['baseline_mode'] == baseline_mode_id
    assert row['contextual_mode'] == contextual_mode_id
    
    db_connection.rollback()


@patch('db.import_review.get_db_connection')
def test_graceful_degradation_db_unreachable(mock_conn, temp_comparison_dir, capsys):
    """AC4: Graceful degradation when DB unreachable."""
    mock_conn.side_effect = psycopg2.OperationalError("Connection refused")
    
    (temp_comparison_dir / "baseline.json").write_text(json.dumps([
        {"review": json.dumps([{"severity": "info", "issue": "Test", "file_path": "x.py"}])}
    ]))
    
    # Should exit 0, not raise
    with pytest.raises(SystemExit) as exc_info:
        import_comparison(
            temp_comparison_dir,
            model="test-model",
            provider="anthropic"
        )
    
    assert exc_info.value.code == 0
    
    captured = capsys.readouterr()
    assert "Database unreachable" in captured.err
    assert "Comparison results saved to JSON only" in captured.err

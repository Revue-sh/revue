"""
Tests for Review Knowledge Base Schema (REVUE-89 + v2 updates)

Schema v2 changes:
- Removed agents table (redundant with reviews.model_id)
- Removed findings.agent_id column
- AI model now tracked at review level only

Test Cases:
- test_db_connection: Verify psycopg2 connects to Postgres
- test_schema_creation: All tables exist with correct structure
- test_seed_data_populated: Reference tables have expected rows
- test_schema_version_tracking: schema_version table enforces single row
- test_gap_1_model_tracking_via_reviews: AI model tracked via reviews.model_id (not findings.agent_id)
- test_gap_2_rated_by_constraint: finding_quality.rated_by has FK to rating_sources
- test_gap_3_schema_version: schema_version table exists with constraints
- test_gap_4_comparison_runs: comparison_runs junction table exists
- test_gap_5_pattern_match_tracking: finding_pattern_matches has matched_by and matched_at
- test_gap_6_raw_json_comment: Migration SQL has inline comment explaining redundancy
"""

import pytest
psycopg2 = pytest.importorskip("psycopg2")  # skip entire module if psycopg2 not installed
import os
from pathlib import Path


@pytest.fixture(scope="module")
def db_connection():
    """
    Fixture providing a database connection for all tests.
    Requires DATABASE_URL in environment.
    """
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        pytest.skip("DATABASE_URL not set. Run: source ~/.zshenv")
    
    conn = psycopg2.connect(database_url)
    yield conn
    conn.close()


def test_db_connection(db_connection):
    """AC5: psycopg2 connection test passes."""
    cursor = db_connection.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()[0]
    
    assert "PostgreSQL" in version
    assert "16" in version  # Expecting Postgres 16.x
    cursor.close()


def test_schema_creation(db_connection):
    """AC1: Migration script creates all tables with correct FKs and constraints."""
    cursor = db_connection.cursor()
    
    # List all expected tables (schema v2: agents table removed)
    expected_tables = [
        'schema_version',
        'severity_levels', 'review_modes', 'models', 'tiers',
        'quality_dimensions', 'fp_reasons', 'pattern_types', 'rating_sources',
        'reviews', 'findings', 'finding_quality', 'finding_outcomes',
        'pr_descriptions', 'pr_description_sections', 'comparison_runs',
        'allowed_patterns', 'disallowed_patterns', 'finding_pattern_matches'
    ]
    
    # Query pg_catalog for table list
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    
    actual_tables = [row[0] for row in cursor.fetchall()]
    
    for table in expected_tables:
        assert table in actual_tables, f"Table '{table}' not found in schema"
    
    cursor.close()


def test_seed_data_populated(db_connection):
    """AC2: Seed data inserts reference rows."""
    cursor = db_connection.cursor()
    
    # Expected counts for each reference table (schema v2: agents removed)
    expected_data = {
        'severity_levels': 5,      # critical, high, medium, low, info
        'review_modes': 2,         # baseline, contextual
        'models': 5,               # claude-sonnet-4-5, claude-haiku-4-5, etc.
        'tiers': 3,                # free, pro, enterprise
        'quality_dimensions': 2,   # clarity, actionability
        'fp_reasons': 7,           # intentional_pattern, test_code, etc.
        'pattern_types': 2,        # allowed, disallowed
        'rating_sources': 2,       # human, auto
    }
    
    for table, expected_count in expected_data.items():
        cursor.execute(f"SELECT COUNT(*) FROM {table};")
        actual_count = cursor.fetchone()[0]
        assert actual_count >= expected_count, \
            f"{table}: expected at least {expected_count} rows, got {actual_count}"
    
    # Verify specific values exist
    cursor.execute("SELECT name FROM severity_levels ORDER BY name;")
    severities = [row[0] for row in cursor.fetchall()]
    assert severities == ['critical', 'high', 'info', 'low', 'medium']
    
    cursor.execute("SELECT name FROM review_modes ORDER BY name;")
    modes = [row[0] for row in cursor.fetchall()]
    assert modes == ['baseline', 'contextual']
    
    cursor.execute("SELECT name FROM rating_sources ORDER BY name;")
    sources = [row[0] for row in cursor.fetchall()]
    assert sources == ['auto', 'human']
    
    cursor.close()


def test_schema_version_tracking(db_connection):
    """AC3: schema_version table present with current version."""
    cursor = db_connection.cursor()
    
    # Query schema_version
    cursor.execute("SELECT version, description FROM schema_version;")
    rows = cursor.fetchall()
    
    # Should have exactly one row
    assert len(rows) == 1, f"Expected 1 row in schema_version, got {len(rows)}"
    
    version, description = rows[0]
    assert version >= 2, f"Expected version >= 2 (after migration 002), got {version}"
    assert "revue-94" in description.lower() or "pattern" in description.lower(), \
        f"Expected schema_version description about REVUE-94 or pattern support, got: {description}"
    
    # Attempt to insert duplicate version (should fail due to PRIMARY KEY)
    with pytest.raises(psycopg2.IntegrityError):
        cursor.execute(f"INSERT INTO schema_version (version, description) VALUES ({version}, 'duplicate');")
        db_connection.commit()
    
    db_connection.rollback()  # Rollback failed transaction
    cursor.close()


def test_gap_1_model_tracking_via_reviews(db_connection):
    """Gap 1 (v2): AI model tracked at review level, not finding level."""
    cursor = db_connection.cursor()
    
    # Verify reviews.model_id FK to models table exists
    cursor.execute("""
        SELECT tc.constraint_name, kcu.column_name, ccu.table_name AS foreign_table_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_name = 'reviews' 
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = 'model_id';
    """)
    
    fk_info = cursor.fetchone()
    assert fk_info is not None, "reviews.model_id FK constraint not found"
    assert fk_info[2] == 'models', f"Expected FK to 'models', got '{fk_info[2]}'"
    
    # Verify findings does NOT have agent_id column (removed in migration 002)
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'findings' AND column_name = 'agent_id';
    """)
    
    agent_id_col = cursor.fetchone()
    assert agent_id_col is None, "findings.agent_id should be removed in schema v2"
    
    cursor.close()


def test_gap_2_rated_by_constraint(db_connection):
    """Gap 2: finding_quality.rated_by_id has FK to rating_sources."""
    cursor = db_connection.cursor()
    
    # Verify finding_quality.rated_by_id FK constraint
    cursor.execute("""
        SELECT tc.constraint_name, kcu.column_name, ccu.table_name AS foreign_table_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_name = 'finding_quality' 
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = 'rated_by_id';
    """)
    
    fk_info = cursor.fetchone()
    assert fk_info is not None, "finding_quality.rated_by_id FK constraint not found"
    assert fk_info[2] == 'rating_sources', \
        f"Expected FK to 'rating_sources', got '{fk_info[2]}'"
    
    cursor.close()


def test_gap_3_schema_version(db_connection):
    """Gap 3: schema_version single-row table exists."""
    cursor = db_connection.cursor()
    
    # Verify table exists
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_name = 'schema_version';
    """)
    
    assert cursor.fetchone() is not None, "schema_version table not found"
    
    # Verify unique index enforcing singleton pattern
    cursor.execute("""
        SELECT indexname, indexdef 
        FROM pg_indexes 
        WHERE tablename = 'schema_version' AND indexname = 'idx_schema_version_singleton';
    """)
    
    index_info = cursor.fetchone()
    assert index_info is not None, "Singleton index not found on schema_version"
    
    # Verify single row
    cursor.execute("SELECT COUNT(*) FROM schema_version;")
    row_count = cursor.fetchone()[0]
    assert row_count == 1, f"Expected 1 row in schema_version, got {row_count}"
    
    cursor.close()


def test_gap_4_comparison_runs(db_connection):
    """Gap 4: comparison_runs junction table exists."""
    cursor = db_connection.cursor()
    
    # Verify table exists with expected columns
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'comparison_runs' 
        ORDER BY ordinal_position;
    """)
    
    columns = [row[0] for row in cursor.fetchall()]
    assert 'baseline_review_id' in columns
    assert 'contextual_review_id' in columns
    assert 'suppression_rate' in columns
    
    # Verify FKs to reviews table
    cursor.execute("""
        SELECT kcu.column_name, ccu.table_name AS foreign_table_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_name = 'comparison_runs' 
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name IN ('baseline_review_id', 'contextual_review_id');
    """)
    
    fks = cursor.fetchall()
    assert len(fks) == 2, f"Expected 2 FKs to reviews, got {len(fks)}"
    
    for fk in fks:
        assert fk[1] == 'reviews', f"Expected FK to 'reviews', got '{fk[1]}'"
    
    cursor.close()


def test_gap_5_pattern_match_tracking(db_connection):
    """Gap 5: finding_pattern_matches has matched_by and matched_at columns."""
    cursor = db_connection.cursor()
    
    # Verify columns exist
    cursor.execute("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns 
        WHERE table_name = 'finding_pattern_matches'
          AND column_name IN ('matched_by', 'matched_at')
        ORDER BY column_name;
    """)
    
    columns = {row[0]: row[1] for row in cursor.fetchall()}
    
    assert 'matched_by' in columns, "matched_by column not found"
    assert 'matched_at' in columns, "matched_at column not found"
    
    # Verify matched_by column type (CHECK constraint validation via actual type)
    cursor.execute("""
        SELECT data_type 
        FROM information_schema.columns 
        WHERE table_name = 'finding_pattern_matches' AND column_name = 'matched_by';
    """)
    
    matched_by_type = cursor.fetchone()[0]
    assert 'character' in matched_by_type, f"Expected VARCHAR, got {matched_by_type}"
    
    # Verify matched_at is timestamp
    cursor.execute("""
        SELECT data_type 
        FROM information_schema.columns 
        WHERE table_name = 'finding_pattern_matches' AND column_name = 'matched_at';
    """)
    
    matched_at_type = cursor.fetchone()[0]
    assert 'timestamp' in matched_at_type, f"Expected TIMESTAMP, got {matched_at_type}"
    
    cursor.close()


def test_gap_6_raw_json_comment(db_connection):
    """Gap 6: Migration SQL has inline comment explaining raw_json redundancy."""
    migration_file = Path(__file__).parent.parent.parent / "src/db/migrations/001_initial_schema.sql"
    
    assert migration_file.exists(), f"Migration file not found: {migration_file}"
    
    sql = migration_file.read_text()
    
    # Verify comment exists explaining raw_json redundancy
    assert "Gap 6" in sql, "Gap 6 marker not found in migration SQL"
    assert "raw_json" in sql.lower(), "raw_json not mentioned in migration SQL"
    assert "redundan" in sql.lower(), "Redundancy explanation not found"
    
    # Verify comment is near the reviews table definition
    reviews_section = sql[sql.find("CREATE TABLE IF NOT EXISTS reviews"):sql.find("CREATE TABLE IF NOT EXISTS findings")]
    assert "raw_json" in reviews_section.lower(), "raw_json comment not in reviews table section"


# Bonus: Test foreign key cascade behavior
def test_cascade_deletes(db_connection):
    """Verify ON DELETE CASCADE works for findings."""
    cursor = db_connection.cursor()
    
    # Query for CASCADE rules on findings table
    cursor.execute("""
        SELECT rc.delete_rule, rc.constraint_name
        FROM information_schema.referential_constraints AS rc
        WHERE rc.constraint_schema = 'public'
          AND rc.constraint_name IN (
              SELECT tc.constraint_name
              FROM information_schema.table_constraints AS tc
              WHERE tc.table_schema = 'public' 
                AND tc.table_name = 'findings'
                AND tc.constraint_type = 'FOREIGN KEY'
          )
          AND rc.delete_rule = 'CASCADE';
    """)
    
    # Should have at least one CASCADE rule (review_id FK)
    cascades = cursor.fetchall()
    assert len(cascades) > 0, "Expected CASCADE delete rules on findings table"
    
    cursor.close()

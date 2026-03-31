# REVUE-89: Normalised Review Knowledge Base Schema (Postgres)

**Status:** In Progress  
**Started:** 2026-03-31  
**Epic:** [REVUE-87 — Review Intelligence & Knowledge Base](https://urukia.atlassian.net/browse/REVUE-87)  
**Jira:** [REVUE-89](https://urukia.atlassian.net/browse/REVUE-89)

---

## User Story

As a developer, I want a normalised Postgres schema for storing review comparisons so that schema changes have minimal impact and analytical queries are fast and reliable.

---

## Background

Schema designed in party mode session 2026-03-31. Six gaps identified from initial 3NF design that need to be addressed for a robust knowledge base.

The schema will support:
- Storing baseline vs contextual review comparisons
- Tracking false positives and patterns
- Human and automated quality ratings
- Agent performance metrics
- PR description context tracking

---

## Six Gaps to Address

1. **Gap 1:** `agents` table + `agent_id` FK on findings
2. **Gap 2:** `rated_by` on `finding_quality` as FK to `rating_sources` or CHECK constraint
3. **Gap 3:** `schema_version` single-row table for migration tracking
4. **Gap 4:** `comparison_runs` junction table replaces `comparisons` (supports A/B description testing)
5. **Gap 5:** `finding_pattern_matches.matched_by` + `matched_at` columns
6. **Gap 6:** `raw_json` redundancy documented with inline comment

---

## Acceptance Criteria

**AC1:** Migration script creates all tables with correct FKs and constraints.

**AC2:** Seed data inserts reference rows:
- `severity_levels` (critical, high, medium, low, info)
- `review_modes` (baseline, contextual)
- `models` (claude-sonnet-4-5, claude-haiku-4-5, etc.)
- `tiers` (free, pro, enterprise)
- `quality_dimensions` (clarity, actionability)
- `fp_reasons` (intentional_pattern, test_code, out_of_scope, etc.)
- `pattern_types` (allowed, disallowed)
- `rating_sources` (human, auto)
- `agents` (code-quality, architecture, security, etc.)

**AC3:** `schema_version` table present with version=1.

**AC4:** All six gaps addressed in the schema.

**AC5:** psycopg2 connection test passes.

---

## Test Cases

**test_schema_creation:**
- Run migration script
- Assert all tables exist
- Assert all FKs are created
- Assert all constraints are enforced

**test_seed_data_populated:**
- Query each reference table
- Assert expected rows exist
- Assert no duplicate entries

**test_schema_version_tracking:**
- Query schema_version table
- Assert version = 1
- Assert only one row exists

**test_db_connection:**
- Connect using DATABASE_URL from env
- Execute SELECT version()
- Assert Postgres 16.x returned

**test_gap_1_agents_table:**
- Verify agents table exists
- Verify findings.agent_id FK constraint

**test_gap_2_rated_by_constraint:**
- Verify finding_quality.rated_by FK or CHECK

**test_gap_3_schema_version:**
- Verify schema_version table exists
- Verify single-row constraint

**test_gap_4_comparison_runs:**
- Verify comparison_runs junction table
- Verify FK to reviews (baseline + contextual)

**test_gap_5_pattern_match_tracking:**
- Verify finding_pattern_matches.matched_by
- Verify finding_pattern_matches.matched_at

**test_gap_6_raw_json_comment:**
- Read migration SQL
- Assert inline comment explaining redundancy

---

## Out of Scope

- Data migration from any existing source
- Query optimization (indexes will be added iteratively)
- External internet access to DB
- CI integration (local/manual workflow only)

---

## Dependencies

**REVUE-88** ✅ DONE — Postgres container running locally on Mac mini (localhost:5432)

**Resolution:**
- Container: `revue-db`, postgres:16-alpine
- Credentials in `~/.zshenv`
- Data persists on `/Volumes/Lexar SSD/Projects/revue.io/postgres-data`

---

## Implementation Plan

### 1. Create Migration Script

**File:** `src/db/migrations/001_initial_schema.sql`

**Structure:**
```sql
-- Schema version tracking (single row table)
CREATE TABLE schema_version (...);

-- Reference tables (lookups)
CREATE TABLE severity_levels (...);
CREATE TABLE review_modes (...);
CREATE TABLE models (...);
CREATE TABLE tiers (...);
CREATE TABLE quality_dimensions (...);
CREATE TABLE fp_reasons (...);
CREATE TABLE pattern_types (...);
CREATE TABLE rating_sources (...);
CREATE TABLE agents (...);

-- Core tables
CREATE TABLE reviews (...);
CREATE TABLE findings (...);
CREATE TABLE finding_quality (...);
CREATE TABLE finding_outcomes (...);
CREATE TABLE pr_descriptions (...);
CREATE TABLE pr_description_sections (...);
CREATE TABLE comparison_runs (...);
CREATE TABLE allowed_patterns (...);
CREATE TABLE disallowed_patterns (...);
CREATE TABLE finding_pattern_matches (...);

-- Seed data
INSERT INTO severity_levels ...;
INSERT INTO review_modes ...;
-- etc.
```

### 2. Create Python Migration Runner

**File:** `src/db/migrate.py`

```python
import psycopg2
import os
from pathlib import Path

def run_migration(migration_file):
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cursor = conn.cursor()
    
    sql = Path(migration_file).read_text()
    cursor.execute(sql)
    
    conn.commit()
    cursor.close()
    conn.close()
```

### 3. Test Connection

**File:** `tests/test_db_connection.py`

```python
import psycopg2
import os

def test_connection():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()[0]
    assert "PostgreSQL 16" in version
    cursor.close()
    conn.close()
```

### 4. Run Migration

```bash
source ~/.zshenv
python3 src/db/migrate.py src/db/migrations/001_initial_schema.sql
```

### 5. Validate Schema

```bash
psql $DATABASE_URL -c "\dt"  # List tables
psql $DATABASE_URL -c "SELECT * FROM schema_version;"
psql $DATABASE_URL -c "SELECT * FROM agents;"
```

---

## Technical Notes

**Postgres Connection:**
- Host: localhost
- Port: 5432
- Database: revue_reviews
- User: revue
- Password: revue_reviews_2026

**Environment Variables (in ~/.zshenv):**
```bash
export DATABASE_URL="postgresql://revue:revue_reviews_2026@localhost:5432/revue_reviews"
export POSTGRES_HOST="localhost"
export POSTGRES_PORT="5432"
export POSTGRES_DB="revue_reviews"
export POSTGRES_USER="revue"
export POSTGRES_PASSWORD="revue_reviews_2026"
```

**Dependencies:**
```bash
pip install psycopg2-binary  # or psycopg2 if built from source
```

---

## DoD Checklist Progress

- [x] Story has clear acceptance criteria
- [x] Dependencies resolved (REVUE-88 Done)
- [ ] Migration script created
- [ ] Reference data seeded
- [ ] Schema version table created
- [ ] All six gaps addressed
- [ ] Unit tests written
- [ ] Tests pass
- [ ] Manual validation complete
- [ ] PR created
- [ ] CI passes
- [ ] PR merged
- [ ] Jira updated to Done

---

## Session Notes

**Started:** 2026-03-31 16:36 GMT+1  
**Agent:** bmad-master  
**Context:** Following E8 epic refinement session. All stories validated and ready.


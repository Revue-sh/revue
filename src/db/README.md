# Revue.io Knowledge Base — Database

**Story:** [REVUE-89](https://urukia.atlassian.net/browse/REVUE-89)  
**Schema Version:** 1  
**Created:** 2026-03-31

---

## Overview

Postgres-backed knowledge base for storing review comparisons, tracking false positives, rating finding quality, and enabling data-driven prompt tuning.

**Database:**
- Host: `localhost:5432` (Rancher Desktop container `revue-db`)
- Database: `revue_reviews`
- User: `revue`
- Connection: `$DATABASE_URL` (from `~/.zshenv`)

---

## Quick Start

### 1. Run Migration

```bash
cd ~/Projects/revue.io  # Or wherever your project lives
source ~/.zshenv        # Load DATABASE_URL
python3 src/db/migrate.py src/db/migrations/001_initial_schema.sql
```

### 2. Verify Schema

```bash
docker exec revue-db psql -U revue -d revue_reviews -c "\dt"  # List tables
docker exec revue-db psql -U revue -d revue_reviews -c "SELECT * FROM schema_version;"
docker exec revue-db psql -U revue -d revue_reviews -c "SELECT name FROM agents;"
```

### 3. Run Tests

```bash
pytest tests/db/test_schema.py -v
```

Expected: **11 passed**

---

## Schema Structure

### Core Tables

| Table | Purpose |
|-------|---------|
| **reviews** | Each review run (baseline or contextual) |
| **findings** | Individual AI findings from a review |
| **finding_quality** | Human and auto ratings (clarity, actionability) |
| **finding_outcomes** | False positive tracking |
| **comparison_runs** | Baseline vs contextual pairs (suppression tracking) |

### Context Tracking

| Table | Purpose |
|-------|---------|
| **pr_descriptions** | PR context used in reviews (sha256 deduplication) |
| **pr_description_sections** | Parsed sections (summary, out of scope, etc.) |

### Pattern Suppression

| Table | Purpose |
|-------|---------|
| **allowed_patterns** | `.revue.yml` patterns (intentional decisions) |
| **disallowed_patterns** | `.revue.yml` anti-patterns |
| **finding_pattern_matches** | Tracks which findings matched which patterns |

### Reference Tables (Lookups)

| Table | Values |
|-------|--------|
| **severity_levels** | critical, high, medium, low, info |
| **review_modes** | baseline, contextual |
| **models** | claude-sonnet-4-5, claude-haiku-4-5, gpt-4-turbo, etc. |
| **tiers** | free, pro, enterprise |
| **quality_dimensions** | clarity, actionability |
| **fp_reasons** | intentional_pattern, test_code, out_of_scope, etc. |
| **pattern_types** | allowed, disallowed |
| **rating_sources** | human, auto |
| **agents** | orchestrator, code-quality, architecture, etc. |

### Schema Versioning

| Table | Purpose |
|-------|---------|
| **schema_version** | Tracks migration version (single-row table, currently v1) |

---

## Six Gaps Addressed

The initial schema design had six gaps identified in party mode (2026-03-31). All addressed:

1. ✅ **Agents table** — `agents` table created with FK from `findings.agent_id`
2. ✅ **Rating source constraint** — `finding_quality.rated_by_id` FK to `rating_sources`
3. ✅ **Schema versioning** — `schema_version` table with singleton constraint
4. ✅ **Comparison runs** — `comparison_runs` junction table for A/B testing support
5. ✅ **Pattern match tracking** — `finding_pattern_matches.matched_by` + `matched_at` columns
6. ✅ **Raw JSON redundancy** — Documented inline in migration SQL

---

## Usage Examples

### Query Schema Version

```sql
SELECT * FROM schema_version;
```

### List All Review Runs

```sql
SELECT r.id, r.ticket_id, m.name AS model, rm.name AS mode, r.run_at, r.total_findings
FROM reviews r
JOIN models m ON r.model_id = m.id
JOIN review_modes rm ON r.mode_id = rm.id
ORDER BY r.run_at DESC
LIMIT 10;
```

### Find High Severity Findings

```sql
SELECT f.id, f.file_path, s.name AS severity, f.issue
FROM findings f
JOIN severity_levels s ON f.severity_id = s.id
WHERE s.name IN ('critical', 'high')
LIMIT 20;
```

### Check Agent Performance (Average Clarity)

```sql
SELECT a.name AS agent, AVG(fq.score) AS avg_clarity
FROM finding_quality fq
JOIN findings f ON fq.finding_id = f.id
JOIN agents a ON f.agent_id = a.id
JOIN quality_dimensions qd ON fq.dimension_id = qd.id
WHERE qd.name = 'clarity'
GROUP BY a.name
ORDER BY avg_clarity DESC;
```

### False Positive Rate by Agent

```sql
SELECT a.name AS agent, 
       COUNT(*) AS total_findings,
       SUM(CASE WHEN fo.is_false_positive THEN 1 ELSE 0 END) AS false_positives,
       ROUND(100.0 * SUM(CASE WHEN fo.is_false_positive THEN 1 ELSE 0 END) / COUNT(*), 2) AS fp_rate_pct
FROM findings f
JOIN agents a ON f.agent_id = a.id
LEFT JOIN finding_outcomes fo ON f.id = fo.finding_id
GROUP BY a.name
ORDER BY fp_rate_pct DESC;
```

---

## Migrations

All migrations are in `src/db/migrations/` with sequential numbering:

- `001_initial_schema.sql` — Initial schema (20 tables + seed data)

**Adding a new migration:**

1. Create `002_your_migration_name.sql`
2. Update `schema_version` in the migration:
   ```sql
   INSERT INTO schema_version (version, description)
   VALUES (2, 'Description of changes');
   ```
3. Run: `python3 src/db/migrate.py src/db/migrations/002_your_migration_name.sql`

---

## Testing

All tests are in `tests/db/test_schema.py`.

**Test coverage:**
- ✅ Database connection (Postgres 16)
- ✅ All 20 tables exist
- ✅ Seed data populated (9 reference tables)
- ✅ Schema version tracking (singleton constraint)
- ✅ Gap 1: Agents table + FK
- ✅ Gap 2: Rating source FK
- ✅ Gap 3: Schema version table
- ✅ Gap 4: Comparison runs junction table
- ✅ Gap 5: Pattern match tracking columns
- ✅ Gap 6: Raw JSON comment in migration
- ✅ Cascade delete behavior

**Run tests:**
```bash
source ~/.zshenv
pytest tests/db/test_schema.py -v
```

---

## Backup & Restore

### Backup

```bash
docker exec revue-db pg_dump -U revue revue_reviews > backup_$(date +%Y%m%d).sql
```

### Restore

```bash
cat backup_20260331.sql | docker exec -i revue-db psql -U revue -d revue_reviews
```

---

## Connection Info

All credentials are in `~/.zshenv`:

```bash
export DATABASE_URL="postgresql://revue:revue_reviews_2026@localhost:5432/revue_reviews"
export POSTGRES_HOST="localhost"
export POSTGRES_PORT="5432"
export POSTGRES_DB="revue_reviews"
export POSTGRES_USER="revue"
export POSTGRES_PASSWORD="revue_reviews_2026"
```

**Python usage:**

```python
import psycopg2
import os

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cursor = conn.cursor()
cursor.execute("SELECT * FROM schema_version;")
print(cursor.fetchone())
conn.close()
```

---

## Next Stories

- **REVUE-90:** `run-comparison.sh` writes to this DB
- **REVUE-91:** `reviews.py` query CLI
- **REVUE-92:** Human rating TUI
- **REVUE-93:** Auto-heuristic scorer
- **REVUE-94:** `.revue.yml` pattern support

---

## Notes

- Data persists on external Lexar SSD: `/Volumes/Lexar SSD/Projects/revue.io/postgres-data`
- Rancher Desktop must be running for DB access
- Originally planned for QNAP NAS (192.168.0.36), pivoted to local for sandbox access

# Revue.io Knowledge Base — Database

**Story:** [REVUE-89](https://urukia.atlassian.net/browse/REVUE-89)  
**Schema Version:** 2  
**Created:** 2026-03-31  
**Updated:** 2026-03-31 (v2: removed agents table)

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
docker exec revue-db psql -U revue -d revue_reviews -c "SELECT name FROM models;"  # AI models
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

### Schema Versioning

| Table | Purpose |
|-------|---------|
| **schema_version** | Tracks migration version (single-row table, currently v2) |

---

## Schema Evolution

### Version 2 (2026-03-31)
**Changes:** Removed `agents` table and `findings.agent_id` column.

**Rationale:** AI model tracking already exists via `reviews.model_id` FK to `models` table. The `agents` table conflated AI model (Claude, GPT) with review category (security, performance). Category is now stored in `findings.category` column.

**Migration:** `002_drop_agents_table.sql`

### Version 1 (2026-03-31)
**Initial schema:** 20 tables addressing six architectural gaps:

1. ~~Agents table~~ (removed in v2, use `reviews.model_id` instead)
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

### Check Model Performance (Average Clarity)

```sql
SELECT m.name AS model, AVG(fq.score) AS avg_clarity
FROM finding_quality fq
JOIN findings f ON fq.finding_id = f.id
JOIN reviews r ON f.review_id = r.id
JOIN models m ON r.model_id = m.id
JOIN quality_dimensions qd ON fq.dimension_id = qd.id
WHERE qd.name = 'clarity'
GROUP BY m.name
ORDER BY avg_clarity DESC;
```

### False Positive Rate by Model

```sql
SELECT m.name AS model, 
       COUNT(*) AS total_findings,
       SUM(CASE WHEN fo.is_false_positive THEN 1 ELSE 0 END) AS false_positives,
       ROUND(100.0 * SUM(CASE WHEN fo.is_false_positive THEN 1 ELSE 0 END) / COUNT(*), 2) AS fp_rate_pct
FROM findings f
JOIN reviews r ON f.review_id = r.id
JOIN models m ON r.model_id = m.id
LEFT JOIN finding_outcomes fo ON f.id = fo.finding_id
GROUP BY m.name
ORDER BY fp_rate_pct DESC;
```

### Findings by Category

```sql
SELECT f.category, COUNT(*) AS count
FROM findings f
GROUP BY f.category
ORDER BY count DESC;
```

---

## Migrations

All migrations are in `src/db/migrations/` with sequential numbering:

- `001_initial_schema.sql` — Initial schema (20 tables + seed data)
- `002_drop_agents_table.sql` — Remove agents table (redundant with reviews.model_id)

**Adding a new migration:**

1. Create `00X_your_migration_name.sql`
2. Update `schema_version` in the migration:
   ```sql
   UPDATE schema_version SET version = X, description = 'Description of changes';
   ```
3. Run: `python3 src/db/migrate.py src/db/migrations/00X_your_migration_name.sql`

---

## Testing

All tests are in `tests/db/test_schema.py`.

**Test coverage:**
- ✅ Database connection (Postgres 16)
- ✅ All 19 tables exist (v2: agents removed)
- ✅ Seed data populated (8 reference tables)
- ✅ Schema version tracking (singleton constraint)
- ✅ Model tracking via reviews.model_id (not findings.agent_id)
- ✅ Rating source FK constraint
- ✅ Schema version table
- ✅ Comparison runs junction table
- ✅ Pattern match tracking columns
- ✅ Raw JSON comment in migration
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
export DATABASE_URL="postgresql://your_user:your_password@localhost:5432/revue_reviews"
export POSTGRES_HOST="localhost"
export POSTGRES_PORT="5432"
export POSTGRES_DB="revue_reviews"
export POSTGRES_USER="your_user"
export POSTGRES_PASSWORD="your_secure_password"
```

**Note:** Replace `your_user` and `your_password` with your actual database credentials. Never commit credentials to git.

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

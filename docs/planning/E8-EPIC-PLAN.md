# E8 Epic: Review Intelligence & Knowledge Base — Implementation Plan

**Epic:** [REVUE-87](https://urukia.atlassian.net/browse/REVUE-87)  
**Status:** In Progress  
**Updated:** 2026-03-31  
**Total Stories:** 7 (1 Done, 6 To Do)

---

## Context

This epic implements a Postgres-backed knowledge base for storing review comparisons, enabling:
- Systematic tracking of false positives and patterns
- Quality metrics for AI agents (clarity, actionability)
- Trend analysis across multiple review runs
- Data-driven prompt tuning

**Infrastructure Change:** Originally planned for QNAP NAS (192.168.0.36), pivoted to **local Docker on Mac mini** due to OpenClaw sandbox constraints. Postgres 16 now running via Rancher Desktop at `localhost:5432`, data on external Lexar SSD.

---

## Story Dependencies (Execution Order)

```
REVUE-88 (✅ DONE) → REVUE-89 → REVUE-90 → REVUE-91 → REVUE-92 → REVUE-93
                                                              ↘
                                                          REVUE-94 (parallel with 93)
```

---

## Stories

### ✅ REVUE-88: Postgres Container (LOCAL DOCKER)

**Status:** Done (2026-03-31)  
**Estimate:** 3 points  
**Summary:** Local Postgres 16 container running on Mac mini via Rancher Desktop.

**Completed:**
- Container: `revue-db`, postgres:16-alpine
- Port: `localhost:5432`
- Data volume: `/Volumes/Lexar SSD/Projects/revue.io/postgres-data`
- Credentials: Added to `~/.zshenv` (DATABASE_URL, POSTGRES_*)
- Tested: `docker exec revue-db pg_isready -U revue -d revue_reviews` ✅

**Next:** Run schema migration (REVUE-89)

---

### 📋 REVUE-89: Normalised Review Knowledge Base Schema (Postgres)

**Status:** To Do  
**Estimate:** 5 points  
**Dependencies:** REVUE-88 (Done)

**Goal:** Create normalised Postgres schema for storing review comparisons.

**Schema Design (from party mode 2026-03-31):**

**Tables:**
1. **reviews** - Core review runs (ticket_id, branch, model, tier, mode, timestamp)
2. **findings** - Individual AI findings (review_id, agent_id, file_path, severity, issue, details, recommendation)
3. **agents** - AI agent catalog (agent_name, version)
4. **finding_quality** - Human/auto ratings (finding_id, clarity, actionability, rated_by)
5. **finding_outcomes** - False positive tracking (finding_id, is_fp, fp_reason)
6. **comparison_runs** - Baseline vs contextual pairs (baseline_review_id, contextual_review_id, suppression_rate)
7. **pr_descriptions** - PR context used (ticket_id, description_text, sha256_hash)
8. **pr_description_sections** - Parsed sections (pr_description_id, section_type, content)
9. **finding_pattern_matches** - Pattern suppression tracking (finding_id, pattern_id, matched_by, matched_at)
10. **allowed_patterns** / **disallowed_patterns** - .revue.yml patterns
11. **schema_version** - Migration tracking (version=1)

**Reference tables** (seed data):
- severity_levels, review_modes, models, tiers, quality_dimensions, fp_reasons, pattern_types, rating_sources

**Six Gaps Addressed:**
1. `agents` table + `agent_id` FK on findings
2. `rated_by` FK to `rating_sources` or CHECK constraint
3. `schema_version` single-row table
4. `comparison_runs` junction table (supports A/B testing)
5. `finding_pattern_matches.matched_by` + `matched_at` columns
6. `raw_json` redundancy documented with inline comment

**Acceptance Criteria:**
- AC1: Migration script creates all tables with correct FKs and constraints
- AC2: Seed data populates reference rows
- AC3: `schema_version` table present with version=1
- AC4: All six gaps addressed
- AC5: Connection test passes: `psycopg2.connect(DATABASE_URL)`

**Migration Script Location:** `src/db/migrations/001_initial_schema.sql` (to create)

---

### 📋 REVUE-90: run-comparison.sh Writes to Postgres

**Status:** To Do  
**Estimate:** 5 points  
**Dependencies:** REVUE-89

**Goal:** Automatically write review results to Postgres after each run.

**Acceptance Criteria:**
- AC1: Findings written to `findings` table with correct `mode_id` (baseline/contextual)
- AC2: PR description parsed → `pr_descriptions` + `pr_description_sections`
- AC3: `comparison_runs` row links baseline_review_id and contextual_review_id
- AC4: Graceful degradation: warning if DB unreachable, continue with JSON export
- AC5: Idempotent: re-running same ticket+branch doesn't duplicate rows

**Implementation Notes:**
- Python helper: `src/db/import_review.py` (psycopg2)
- Parse JSON output from `revue` CLI
- Hash PR description (sha256) to detect duplicates
- Transaction: all or nothing (rollback on error)

---

### 📋 REVUE-91: reviews.py Query CLI

**Status:** To Do  
**Estimate:** 8 points  
**Dependencies:** REVUE-90

**Goal:** Python CLI for querying knowledge base without raw SQL.

**Named Queries:**
```bash
reviews.py list                          # All reviews with finding counts
reviews.py show REVUE-XX                 # Full detail for one ticket
reviews.py false-positives [--top N]     # Most recurring FP patterns
reviews.py clarity [--agent NAME]        # Avg clarity score per agent
reviews.py suppression-trend             # Context suppression rate over time
reviews.py patterns                      # Active allowed/disallowed patterns
```

**Acceptance Criteria:**
- AC1: All six queries implemented and tested
- AC2: Graceful error when DB unreachable
- AC3: `--format json|table` output flag

**Tech Stack:**
- Click for CLI
- Rich for table formatting
- psycopg2 for DB connection

---

### 📋 REVUE-92: Human Rating Flow

**Status:** To Do  
**Estimate:** 5 points  
**Dependencies:** REVUE-91

**Goal:** Interactive TUI to rate findings for quality after review.

**Flow:**
```bash
reviews.py rate REVUE-XX
```

**Prompts per finding:**
- Clarity (1-5): How clear is the issue description?
- Actionability (1-5): How specific is the recommendation?
- False positive (y/n): Is this a false alarm?
- FP reason (if yes): Why? (dropdown: intentional_pattern, test_code, out_of_scope, etc.)

**Acceptance Criteria:**
- AC1: Interactive TUI shows each finding, prompts for ratings
- AC2: Scores written to `finding_quality` and `finding_outcomes`
- AC3: Skippable (press Enter to skip)
- AC4: Resumable (already-rated findings skipped)

**Tech Stack:**
- Textual or simple `input()` loops
- Progress indicator (e.g., "Finding 3/12")

---

### 📋 REVUE-93: Auto-Heuristic Quality Scorer

**Status:** To Do  
**Estimate:** 3 points  
**Dependencies:** REVUE-92

**Goal:** Automatically estimate quality for unrated findings.

**Heuristics:**

**Clarity score (1-5):**
- Has `issue` + `details` fields populated? (+2)
- `len(issue) > 20` (+1)
- No vague words (consider, might, perhaps) (+1)
- Contains specific file/line reference (+1)

**Actionability score (1-5):**
- Has `recommendation` field (+2)
- Contains code snippet or file path (+1)
- Uses specific verb (add, remove, change, replace) (+1)
- Mentions exact change needed (+1)

**Acceptance Criteria:**
- AC1: Scorer runs at import time (after REVUE-90 inserts)
- AC2: `rated_by = auto` for auto-scored rows
- AC3: Human ratings (`rated_by = human`) override auto scores
- AC4: Accuracy benchmarked against REVUE-86 human ratings

**Implementation:**
- `src/db/auto_scorer.py` module
- Trigger: Called from `import_review.py` after findings inserted
- Update query: `INSERT INTO finding_quality (finding_id, clarity, actionability, rated_by) ...`

---

### 📋 REVUE-94: .revue.yml Pattern Support

**Status:** To Do  
**Estimate:** 5 points  
**Dependencies:** REVUE-89 (patterns table)

**Goal:** Define allowed/disallowed patterns in `.revue.yml` to suppress known false positives.

**Observed False Positives (REVUE-83/84/86):**
1. `_def` attribute access on `LoadedAgent` (intentional, no public API)
2. Inline lazy `httpx` import (intentional, now module-level)
3. `test_vcs_adapter.py` deletion (coverage exists in `test_vcs_adapters.py`)
4. Bare `except` in `_inject_pr_context` (intentional, must not crash)

**Schema Extension:**
```yaml
noise_filters:
  allowed_patterns:
    - pattern: "_def attribute access (LoadedAgent)"
      rationale: "Internal implementation detail, no public API"
  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as tickets"
```

**Acceptance Criteria:**
- AC1: `.revue.yml` schema extended with `noise_filters`
- AC2: Patterns injected into agent system prompts before review
- AC3: `revue.io/.revue.yml` populated with four patterns above
- AC4: Comparison run shows FP reduction for known patterns
- AC5: Customer docs updated (README + docs/configuration.md)

**Implementation:**
- YAML parser reads patterns → inject into `system_prompt` context
- DB tracking: `allowed_patterns` / `disallowed_patterns` tables
- `finding_pattern_matches` logs which findings matched which patterns

---

## Estimated Effort

| Story | Points | Priority |
|-------|--------|----------|
| REVUE-88 | 3 | ✅ Done |
| REVUE-89 | 5 | P0 |
| REVUE-90 | 5 | P0 |
| REVUE-91 | 8 | P1 |
| REVUE-92 | 5 | P1 |
| REVUE-93 | 3 | P2 |
| REVUE-94 | 5 | P2 |
| **Total** | **34** | |

**Sprint 1 (P0):** REVUE-89, REVUE-90 → Database foundation (10 points)  
**Sprint 2 (P1):** REVUE-91, REVUE-92 → Analytics & rating (13 points)  
**Sprint 3 (P2):** REVUE-93, REVUE-94 → Automation & UX (8 points)

---

## Migration from NAS to Local Docker

**Original Plan:**
- Postgres on QNAP NAS at `192.168.0.36:5432`
- Accessible from any machine on network
- Shared knowledge base across devices

**Current Setup:**
- Postgres on Mac mini via Rancher Desktop at `localhost:5432`
- Data on external Lexar SSD (`/Volumes/Lexar SSD/Projects/revue.io/postgres-data`)
- Single-machine development environment

**Implications:**
- ✅ Simpler setup (no NAS dependencies)
- ✅ Faster iteration (local container)
- ⚠️ Single point of access (Mac mini only)
- ⚠️ Manual backups needed (no NAS redundancy)

**Future:** If multi-machine access needed, can migrate data to NAS by:
1. Export DB: `pg_dump revue_reviews > backup.sql`
2. Start NAS container (per original REVUE-88 plan)
3. Import: `psql -h 192.168.0.36 -U revue revue_reviews < backup.sql`
4. Update `~/.zshenv` connection strings

---

## Testing Strategy

### Unit Tests
- Schema validation (FK constraints, NOT NULL, CHECK)
- Import script idempotency
- Auto-scorer accuracy vs human ratings
- Pattern matching logic

### Integration Tests
- Full review → import → query flow
- Error handling (DB unreachable, malformed JSON)
- Transaction rollback on failure

### E2E Validation
- Run REVUE-86 comparison → import → query → rate → score
- Verify suppression tracking for .revue.yml patterns

---

## Documentation

**Files to create/update:**
- `src/db/README.md` — Schema overview, migration guide
- `docs/knowledge-base.md` — User guide for `reviews.py` CLI
- `docs/configuration.md` — `.revue.yml` pattern syntax
- `src/db/migrations/001_initial_schema.sql` — Schema DDL
- `src/db/import_review.py` — Import script
- `src/db/auto_scorer.py` — Heuristic scorer
- `scripts/reviews.py` — Query CLI

**Existing to update:**
- `README.md` — Add knowledge base section
- `docs/story-dod-checklist.md` — DB testing gate

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Schema changes break existing code | Migration versioning + rollback scripts |
| DB becomes single point of failure | Regular pg_dump backups to git LFS or external storage |
| Import script performance degrades | Batch inserts, index optimization |
| Pattern matching too aggressive | Human review required for first 10 patterns |

---

## Success Metrics

1. **Knowledge base populated:** >50 review runs imported
2. **False positive reduction:** 30%+ suppression with patterns (vs. REVUE-86 baseline)
3. **Rating completion:** >80% of findings rated (human or auto)
4. **Query performance:** <500ms for all named queries on 1000+ findings

---

## Next Session Checklist

Before starting implementation:
- [ ] Verify Postgres container running: `docker ps | grep revue-db`
- [ ] Source env vars: `source ~/.zshenv`
- [ ] Test connection: `psql $DATABASE_URL -c "SELECT version();"`
- [ ] Review schema design from party mode session (this doc captures it)
- [ ] Start with REVUE-89 (schema migration)

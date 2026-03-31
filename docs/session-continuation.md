# Session Continuation
**Updated:** 2026-03-31 | **For:** Next session

## Completed this session

### REVUE-90: Database Import for Review Comparisons
- ✅ **PR #25 merged to main** (`9bfc3dc`)
- ✅ **Jira REVUE-90 → Done**
- ✅ Created `src/db/import_review.py` (447 lines) — Full importer with all 5 ACs
- ✅ Created `tests/db/test_import_review.py` (366 lines) — 11/11 tests passing
- ✅ Updated `scripts/run-comparison.sh` — Auto-imports after generating comparison
- ✅ All acceptance criteria met:
  - AC1: Findings written with correct mode_id (baseline/contextual)
  - AC2: PR description parsed into sections (split on ## headers)
  - AC3: comparison_runs links baseline + contextual reviews
  - AC4: Graceful degradation if DB unreachable (warning + exit 0)
  - AC5: Idempotent (checks existing reviews, SHA256 dedup for PR descriptions)
- ✅ CI/CD fixes:
  - Fixed license validation in CI with `REVUE_TIER_OVERRIDE=Pro` + `APP_ENV=staging`
  - Scoped env vars to review step only (isolated from tests)
  - All 540 tests passing in CI

**Commits:**
- `ff56c36` — Initial implementation (importer + tests)
- `79d3276` — CI fix (scope APP_ENV to review step)
- `4abdcd4` — Trigger pipeline after env var cleanup

**Schema changes:** None (PR #24 already migrated to schema v2 — removed agents table)

### Schema Refactoring (PR #24)
- ✅ **PR #24 merged to main** (`dc49915`)
- ✅ Removed redundant `agents` table (AI model tracked via `reviews.model_id`)
- ✅ Migration 002 applied (schema version bumped to 2)
- ✅ 11/11 schema tests updated and passing

### New Story Created
- ✅ **REVUE-95** created in Jira — Enhanced orchestration logging with tier-based detail levels
  - Priority: Medium
  - Labels: ux, logging, transparency, post-mvp
  - Goal: Show customers which agents reviewed their code and why

---

## Sprint & Epic State

**Epic:** REVUE-87 — Review Intelligence & Knowledge Base  
**Progress:** 3/7 stories complete (43%)

| Story | Status | Priority |
|-------|--------|----------|
| REVUE-88 | ✅ Done | Postgres container (local Docker) |
| REVUE-89 | ✅ Done | Normalised schema (v2) |
| REVUE-90 | ✅ Done | run-comparison.sh DB integration |
| REVUE-91 | 📋 To Do | P1 | reviews.py query CLI |
| REVUE-92 | 📋 To Do | P1 | Human rating TUI |
| REVUE-93 | 📋 To Do | P2 | Auto-heuristic scorer |
| REVUE-94 | 📋 To Do | P2 | .revue.yml pattern support |

**Next Sprint (P1 — Analytics & Rating):** REVUE-91 (8 points), REVUE-92 (5 points)

**Sprint velocity:** 3 stories (13 points) completed this session

---

## Remaining work — next steps

### 1. REVUE-91: reviews.py query CLI (P1, 8 points)
**Goal:** Python CLI for querying knowledge base without raw SQL.

**First action:** Create `src/db/reviews.py` with Click-based CLI framework and first query (`list`).

**Named queries to implement:**
```bash
reviews.py list                          # All reviews with finding counts
reviews.py show REVUE-XX                 # Full detail for one ticket
reviews.py false-positives [--top N]     # Most recurring FP patterns
reviews.py clarity [--model NAME]        # Avg clarity score per model
reviews.py suppression-trend             # Context suppression rate over time
reviews.py patterns                      # Active allowed/disallowed patterns
```

**Tech stack:**
- Click for CLI framework
- Rich for table formatting
- psycopg2 for DB connection (reuse `get_db_connection()` from import_review.py)

**Dependencies:** ✅ REVUE-90 merged (importer works, DB has data once run-comparison.sh executed)

**File to create:** `src/db/reviews.py`

---

### 2. REVUE-92: Human rating TUI (P1, 5 points)
**Blocked by:** REVUE-91 (needs reviews.py CLI foundation)

**Flow:** `reviews.py rate REVUE-XX` → Interactive prompts for each finding → Write to `finding_quality` + `finding_outcomes` tables

**Prompts per finding:**
- Clarity (1-5): How clear is the issue description?
- Actionability (1-5): How specific is the recommendation?
- False positive (y/n): Is this a false alarm?
- FP reason (if yes): Why? (dropdown: intentional_pattern, test_code, out_of_scope, etc.)

**Tech stack:** Textual or simple `input()` loops with progress indicator

---

### 3. REVUE-93: Auto-heuristic scorer (P2, 3 points)
**Blocked by:** REVUE-92 (needs human ratings to benchmark against)

**Trigger:** Called from `import_review.py` after findings inserted  
**File:** `src/db/auto_scorer.py`

**Heuristics:**
- Clarity: Has issue + details? Length > 20 chars? Specific file/line refs?
- Actionability: Has recommendation? Contains code snippet? Specific verb?

---

### 4. REVUE-94: .revue.yml pattern support (P2, 5 points)
**Can run parallel with REVUE-93**

**Goal:** Define allowed/disallowed patterns in `.revue.yml` to suppress known false positives.

**File:** Extend `.revue.yml` schema, inject patterns into agent prompts

---

## Continuation prompt

**Epic:** REVUE-87 (3/7 complete, 43%) — Review Intelligence & Knowledge Base  
**Next story:** REVUE-91 (reviews.py query CLI) — 8 points, P1

**Start here:**
1. Read `docs/E8-EPIC-PLAN.md` for full context
2. Read `docs/session-continuation.md` (this file)
3. Create `src/db/reviews.py` with Click CLI framework
4. Implement first query: `reviews.py list` (show all reviews with finding counts)
5. Branch: `feat/REVUE-91-query-cli` → implement → test → commit → PR

**Database ready:** Postgres running at `localhost:5432`, schema v2 (19 tables), importer working (`src/db/import_review.py`)

**Test data:** Run `./scripts/run-comparison.sh REVUE-XX /path/to/pr_desc.txt` to populate DB with sample data

**Blockers:** None. REVUE-90 merged, importer functional.

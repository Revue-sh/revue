# Session Continuation
**Updated:** 2026-03-31 22:05 GMT+1 | **For:** Next session

## Completed this session

### REVUE-91: reviews.py Query CLI
- ✅ **PR #26 merged to main** (`0e90922`)
- ✅ **Jira REVUE-91 → Done**
- ✅ All 6 queries implemented (list, show, false-positives, clarity, suppression-trend, patterns)
- ✅ Repository pattern + service layer architecture (modular monolith)
- ✅ Created `ARCHITECTURE.md` (14KB) — project-wide standards
- ✅ Files created:
  - `src/db/repositories/base.py` + `review_repository.py` (10 query methods)
  - `src/reviews/service.py` + `models.py` (domain models)
  - `src/cli/reviews.py` (Click CLI with Rich formatting)
  - `src/db/connection.py` (DB connection helper)
  - `scripts/reviews.py` (executable wrapper)
  - `docs/REVUE-91-dod.md` (DoD checklist)
- ✅ All acceptance criteria met:
  - AC1: All six queries implemented and tested ✅
  - AC2: Graceful error when DB unreachable ✅
  - AC3: `--format json|table` output flag ✅
- ✅ Dependencies added: click, rich, psycopg2-binary

**Commands working:**
```bash
./scripts/reviews.py list [--limit N] [--format table|json]
./scripts/reviews.py show REVUE-XX [--format table|json]
./scripts/reviews.py false-positives [--top N] [--format table|json]
./scripts/reviews.py clarity [--model NAME] [--format table|json]
./scripts/reviews.py suppression-trend [--format table|json]
./scripts/reviews.py patterns [--format table|json]
```

**Commits:**
- `a8079aa` — Repository pattern + first 2 queries (list, show)
- `21a5999` — Remaining 4 queries (false-positives, clarity, suppression-trend, patterns)
- `020396e` — DoD checklist
- `2c03cb1` — Increased max_diff_lines to 10000 for review

### REVUE-96: Dynamic Context Window Limits
- ✅ **Story created** — [REVUE-96](https://urukia.atlassian.net/browse/REVUE-96)
- ✅ Goal: Make `max_diff_lines: auto` calculate limit from AI model context window
- ✅ Priority: Medium
- ✅ Labels: enhancement, ai, performance, ux
- ✅ Future-proof: adapts to new models automatically (GPT-5, Claude Opus 5, etc.)

### Documentation Updates
- ✅ Added Bitbucket API credentials to `TOOLS.md`
  - `BITBUCKET_API_TOKEN` and `BITBUCKET_USERNAME` in `~/.zshrc`
  - Example curl commands for PR creation

---

## Sprint & Epic State

**Epic:** REVUE-87 — Review Intelligence & Knowledge Base  
**Progress:** 4/7 stories complete (57%)

| Story | Status | Priority |
|-------|--------|----------|
| REVUE-88 | ✅ Done | Postgres container (local Docker) |
| REVUE-89 | ✅ Done | Normalised schema (v2) |
| REVUE-90 | ✅ Done | run-comparison.sh DB integration |
| REVUE-91 | ✅ Done | reviews.py query CLI |
| REVUE-92 | 📋 To Do | P1 | Human rating TUI |
| REVUE-93 | 📋 To Do | P2 | Auto-heuristic scorer |
| REVUE-94 | 📋 To Do | P2 | .revue.yml pattern support |

**Sprint velocity:** 4 stories (21 points) completed this session

---

## Remaining work — next steps

### 1. REVUE-92: Human rating TUI (P1, 5 points)
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

**Dependencies:** ✅ REVUE-91 done (reviews.py CLI exists)

---

### 2. REVUE-93: Auto-heuristic scorer (P2, 3 points)
**Blocked by:** REVUE-92 (needs human ratings to benchmark against)

**Trigger:** Called from `import_review.py` after findings inserted  
**File:** `src/db/auto_scorer.py`

**Heuristics:**
- **Clarity score (1-5):**
  - Has `issue` + `details` fields populated? (+2)
  - `len(issue) > 20` (+1)
  - No vague words (consider, might, perhaps) (+1)
  - Contains specific file/line reference (+1)

- **Actionability score (1-5):**
  - Has `recommendation` field (+2)
  - Contains code snippet or file path (+1)
  - Uses specific verb (add, remove, change, replace) (+1)
  - Mentions exact change needed (+1)

---

### 3. REVUE-94: .revue.yml pattern support (P2, 5 points)
**Can run parallel with REVUE-93**

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

---

### 4. REVUE-96: Dynamic context window limits (Medium priority)
**Can be implemented anytime**

**Goal:** Replace hardcoded `max_diff_lines` with dynamic calculation based on model context window.

**Implementation:**
- Create `src/revue/context_manager.py`
- Calculate: `available_tokens = model_window - (system + agents + orchestration + PR + response)`
- Convert tokens → lines with safety margin
- Support `max_diff_lines: auto` in config

**Example:** Claude Sonnet 4.5 (200K tokens) → ~27K line limit (vs current 2K/10K)

---

## Continuation prompt

**Epic:** REVUE-87 (4/7 complete, 57%) — Review Intelligence & Knowledge Base  
**Next story:** REVUE-92 (Human rating TUI) — 5 points, P1

**Start here:**
1. Read `docs/E8-EPIC-PLAN.md` for full context
2. Read `docs/session-continuation.md` (this file)
3. Create rating flow in `src/cli/reviews.py` → `reviews.py rate REVUE-XX`
4. Interactive prompts for clarity, actionability, false positive tracking
5. Write to `finding_quality` and `finding_outcomes` tables
6. Branch: `feat/REVUE-92-rating-tui` → implement → test → commit → PR

**Database ready:** Postgres running at `localhost:5432`, schema v2 (19 tables), reviews.py CLI working

**Blockers:** None. REVUE-91 merged, CLI functional.

---

## Architecture Standards Established

**ARCHITECTURE.md created** — Project-wide modular monolith standards:
- Repository pattern for data access abstraction
- Service layer for business logic
- Dependency injection throughout
- SOLID principles enforcement
- Migration path to microservices documented

**All future work must follow these standards.**

---

## Tools & Credentials Reference

See `TOOLS.md` for:
- Jira API credentials (`JIRA_API_TOKEN`)
- Bitbucket API credentials (`BITBUCKET_API_TOKEN`, `BITBUCKET_USERNAME`)
- All credentials in `~/.zshrc` (source first)

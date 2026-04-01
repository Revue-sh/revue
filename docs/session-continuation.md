# Session Continuation
**Updated:** 2026-03-31 23:15 GMT+1 | **For:** Next session

## Completed this session

### REVUE-91: reviews.py Query CLI (PR #26 ✅ Merged)
- **Commit:** `0e90922`
- **Jira:** Done
- **What:** 6 query commands (list, show, false-positives, clarity, suppression-trend, patterns)
- **Architecture:** Repository pattern + service layer (modular monolith)
- **Files created:**
  - `ARCHITECTURE.md` (14KB) — project-wide SOLID standards
  - `src/db/repositories/` (base + review_repository, 10 methods)
  - `src/reviews/` (service + models)
  - `src/cli/reviews.py` (Click CLI with Rich)
  - `src/db/connection.py`
  - `scripts/reviews.py`
  - `docs/REVUE-91-dod.md`
- **Code:** 1,347 lines, all ACs met
- **Impact:** Established modular monolith architecture for entire project

### REVUE-92: Human Rating TUI (PR #27 ✅ Merged)
- **Commit:** `dc080c3`
- **Jira:** Done
- **What:** Interactive `reviews.py rate REVUE-XX` command
- **Features:**
  - Clarity (1-5), actionability (1-5), false positive (y/n) prompts
  - Skip/resume support, progress indicators, graceful quit
  - Repository methods with ON CONFLICT upserts (idempotent)
  - Crash-safe per-finding commits
- **Code:** 320 lines
- **Manual review:** ⭐⭐⭐⭐⭐ 4.8/5.0 (SOLID compliant, no security issues)
- **AI review:** 0 findings (legitimately clean code)

### Stories Created

**REVUE-96:** Dynamic Context Window Limits (Medium priority)
- Make `max_diff_lines: auto` calculate from model context window
- Future-proof: adapts to GPT-5, Claude Opus 5, etc.
- Example: Claude Sonnet 4.5 (200K tokens) → ~27K line limit (vs current 10K)

**REVUE-97:** Enhanced PR Summary Comment (Medium priority)
- Replace "✅ Looks good! · 0 files reviewed" with detailed breakdown
- Show: star rating, category breakdown (Architecture/Security/Performance/Quality), files reviewed, finding counts
- AC6: Update existing comment on re-review (don't post duplicates)
- AC7: Show "Last updated: <timestamp>" and "Review #3"

**REVUE-98:** Auto-Resolve Fixed Comments (**High priority**)
- Auto-resolve inline comments when issues fixed in new commits
- Finding fingerprinting: match old vs new findings
- Resolved comments show: "✅ Fixed in commit abc123"
- Summary delta: "3 resolved, 1 new, 2 still open"
- **Developer pain point:** Currently must manually resolve dozens of comments

### Documentation Updates
- `TOOLS.md` — Added Bitbucket API credentials reference
- `ARCHITECTURE.md` — Created project-wide modular monolith standards
- All commits follow new standards

---

## Sprint & Epic State

**Epic:** REVUE-87 — Review Intelligence & Knowledge Base  
**Progress:** 5/7 stories complete (71%)

| Story | Status | Points | Notes |
|-------|--------|--------|-------|
| REVUE-88 | ✅ Done | 3 | Postgres Docker container |
| REVUE-89 | ✅ Done | 5 | Schema v2 (normalized) |
| REVUE-90 | ✅ Done | 5 | DB import integration |
| REVUE-91 | ✅ Done | 8 | Query CLI |
| REVUE-92 | ✅ Done | 5 | Rating TUI |
| REVUE-93 | 📋 To Do | 3 | Auto-heuristic scorer (P2) |
| REVUE-94 | 📋 To Do | 5 | .revue.yml pattern support (P2) |

**Completed:** 26 points (5 stories)  
**Remaining:** 8 points (2 stories)

**New priority stories (outside epic):**
- REVUE-96 (Medium) — Dynamic context window limits
- REVUE-97 (Medium) — Enhanced PR summary comment
- REVUE-98 (**High**) — Auto-resolve fixed comments

---

## Remaining work — next steps

### 1. REVUE-93: Auto-Heuristic Scorer (P2, 3 points)
**Goal:** Auto-rate finding quality using heuristics (benchmark against human ratings from REVUE-92)

**First action:** Create `src/db/auto_scorer.py`

**Heuristics:**
- **Clarity (1-5):** Has issue+details? Length > 20? Specific file refs? No vague words?
- **Actionability (1-5):** Has recommendation? Code snippet? Specific verb? Exact change?

**Integration:** Call from `import_review.py` after findings inserted

**Dependencies:** ✅ REVUE-92 merged (human ratings available for benchmarking)

---

### 2. REVUE-94: .revue.yml Pattern Support (P2, 5 points)
**Goal:** Define allowed/disallowed patterns in `.revue.yml` to suppress false positives

**First action:** Extend `.revue.yml` schema in `src/revue/config.py`

**Schema:**
```yaml
noise_filters:
  allowed_patterns:
    - pattern: "_def attribute access"
      rationale: "Internal implementation detail"
  disallowed_patterns:
    - pattern: "TODO in production"
      rationale: "Track as tickets"
```

**Integration:** Inject patterns into agent system prompts

**Can run parallel with REVUE-93**

---

### 3. REVUE-98: Auto-Resolve Fixed Comments (**High priority, outside epic**)
**Goal:** Auto-resolve inline comments when issues fixed (major developer pain point)

**First action:** Update `src/revue/platforms/bitbucket.py` to implement finding fingerprinting

**Flow:**
1. Store fingerprints of findings (file+line+issue hash)
2. On re-review, fetch existing comments
3. Compare: finding gone → resolve comment + reply "✅ Fixed in abc123"
4. Update summary: "3 resolved, 1 new, 2 still open"

**Why high priority:** Developers currently must manually resolve dozens of comments — very annoying

---

### 4. REVUE-97: Enhanced PR Summary Comment (Medium priority)
**Goal:** Replace basic "Looks good!" with detailed quality breakdown

**First action:** Update comment formatting in `src/revue/platforms/bitbucket.py`

**Features:**
- Star rating, category breakdown, files reviewed, finding counts
- Update existing comment on re-review (don't post duplicates)
- Show "Last updated: 2h ago" + "Review #3"

**Can implement after REVUE-98** (they work together nicely)

---

### 5. REVUE-96: Dynamic Context Window Limits (Medium priority)
**Goal:** Calculate `max_diff_lines` from model context window

**First action:** Create `src/revue/context_manager.py`

**Calculation:** `available = context_window - (system + agents + orchestration + PR + response)`

**Can implement anytime** (independent of other stories)

---

## Continuation prompt

**Epic REVUE-87:** 5/7 complete (71%) — 2 stories remaining  
**New priorities:** REVUE-98 (High) + REVUE-97 (Medium) for better PR comment UX

**Recommended next action:**
1. **REVUE-98** (Auto-resolve fixed comments) — addresses developer pain point
2. **REVUE-97** (Enhanced summary) — pairs well with REVUE-98
3. **Then:** REVUE-93 + REVUE-94 to finish epic

**Or continue epic:**
- REVUE-93 (Auto-heuristic scorer) — 3 points, ready to start
- REVUE-94 (.revue.yml patterns) — 5 points, can run parallel

**Architecture:** All new code must follow `ARCHITECTURE.md` standards (repository pattern, service layer, SOLID, dependency injection)

**Database:** Postgres at localhost:5432, schema v2, reviews.py CLI working

**Blockers:** None

---

## Session Metrics

**Duration:** ~3.5 hours  
**Stories completed:** 2 (13 points)  
**Stories created:** 3 (REVUE-96, 97, 98)  
**PRs merged:** 2 (both with 0 AI findings — clean architecture!)  
**Code written:** 1,667 lines  
**Documentation:** ARCHITECTURE.md + 3 DoD/planning docs  
**Code quality:** 4.8/5.0 (manual review)

**Epic completion:** 57% → 71% (+14%)

---

## Architecture Standards (Mandatory)

**ARCHITECTURE.md** established project-wide rules:
- Repository pattern (all SQL in repositories/)
- Service layer (business logic)
- Dependency injection (constructor-based)
- Domain models ≠ DB/API schemas
- SOLID principles enforcement

**All future PRs must comply** — see ARCHITECTURE.md for patterns and examples.

---

## Tools & Credentials

See `TOOLS.md`:
- Jira: `JIRA_API_TOKEN` in `~/.zshrc`
- Bitbucket: `BITBUCKET_API_TOKEN`, `BITBUCKET_USERNAME` in `~/.zshrc`
- Source `~/.zshrc` before API calls

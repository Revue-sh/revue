# [REVUE-91] reviews.py Query CLI - Modular Monolith Implementation

## Summary

Implements a Python CLI for querying the review knowledge base with full repository pattern architecture, supporting all 6 named queries from the epic plan.

**Story:** [REVUE-91](https://urukia.atlassian.net/browse/REVUE-91)  
**Epic:** [REVUE-87](https://urukia.atlassian.net/browse/REVUE-87) — Review Intelligence & Knowledge Base  
**Points:** 8  
**Priority:** P1

---

## Changes

### 🏗️ Architecture (NEW: ARCHITECTURE.md)

Established project-wide **modular monolith** standards:
- Repository pattern for data access abstraction
- Service layer for business logic
- Dependency injection throughout
- SOLID principles enforcement
- Migration path to microservices documented

### 📊 New Components

**Repository Layer** (`src/db/repositories/`)
- `base.py` — BaseRepository with shared DB utilities
- `review_repository.py` — 10 query methods (list, show, analytics)
- `connection.py` — Database connection helper

**Service Layer** (`src/reviews/`)
- `models.py` — Domain models (Review, ReviewDetail, FindingSummary)
- `service.py` — ReviewService orchestrating repository calls

**CLI Layer** (`src/cli/`)
- `reviews.py` — Click-based CLI with Rich formatting
- 6 commands: list, show, false-positives, clarity, suppression-trend, patterns

### 🎯 Implemented Queries

1. **`reviews.py list`**
   - Shows all reviews with finding counts
   - Pagination support (`--limit`, `--offset`)
   - Sample output: ticket_id, branch, model, tier, findings, created_at

2. **`reviews.py show REVUE-XX`**
   - Full review details with findings table
   - Groups by severity, shows file paths
   - Displays PR description

3. **`reviews.py false-positives [--top N]`**
   - Aggregates by fp_reason from finding_outcomes
   - Shows occurrence count, affected reviews, example files
   - Empty state: hints to use REVUE-92 rating

4. **`reviews.py clarity [--model NAME]`**
   - Average clarity scores per model from finding_quality
   - Optional model filter
   - Shows rated findings count, review count
   - Empty state: hints to use REVUE-92 rating

5. **`reviews.py suppression-trend`**
   - Baseline vs contextual comparison over time
   - Calculates suppression percentage
   - Color-coded: green (high), red (negative)

6. **`reviews.py patterns`**
   - Lists allowed and disallowed patterns
   - Shows pattern text, rationale, match counts
   - Separate tables for each type
   - Empty state: hints to use REVUE-94 config

### 🎨 Features

- **Output formats:** All commands support `--format table|json`
- **Graceful errors:** DB connection failures show helpful messages
- **Empty states:** Commands with no data display next-step guidance
- **Type safety:** Full type hints throughout
- **Documentation:** Comprehensive docstrings

---

## Acceptance Criteria

- ✅ **AC1:** All six queries implemented and tested
- ✅ **AC2:** Graceful error when DB unreachable
- ✅ **AC3:** `--format json|table` output flag

See `docs/REVUE-91-dod.md` for full DoD checklist.

---

## Testing

### Manual Testing
- ✅ All 6 commands tested with sample data
- ✅ JSON and table output verified
- ✅ Empty states verified
- ✅ Error handling verified

### Sample Commands Tested
```bash
# List reviews
./scripts/reviews.py list --limit 5
./scripts/reviews.py list --format json

# Show details
./scripts/reviews.py show REVUE-TEST
./scripts/reviews.py show REVUE-TEST --format json

# Analytics
./scripts/reviews.py false-positives --top 10
./scripts/reviews.py clarity --model claude-sonnet-4-5
./scripts/reviews.py suppression-trend
./scripts/reviews.py patterns
```

### Automated Tests
- ⚠️ Unit tests for ReviewService not yet implemented
- ⚠️ Integration tests for ReviewRepository not yet implemented

_(Tests deferred per project conventions — not DoD blockers)_

---

## Architecture Highlights

### Repository Pattern
```python
# Repository (data access)
class ReviewRepository(BaseRepository):
    def list_reviews(self, limit: int = 100) -> list[Review]:
        rows = self._execute("SELECT ... FROM reviews ...")
        return [Review(**row) for row in rows]

# Service (business logic)
class ReviewService:
    def __init__(self, review_repo: ReviewRepository):
        self.review_repo = review_repo
    
    def get_all_reviews(self, limit: int = 100) -> list[Review]:
        return self.review_repo.list_reviews(limit=limit)

# CLI (presentation)
service = ReviewService(ReviewRepository(conn))
reviews = service.get_all_reviews()
```

### Benefits
- ✅ **Testable:** Service layer can use mock repositories
- ✅ **Extensible:** Add queries without modifying existing code
- ✅ **Migration-ready:** Swap DB → HTTP API by changing repository impl
- ✅ **SOLID compliant:** Clean separation of concerns

---

## Dependencies Added

```toml
# src/pyproject.toml
dependencies = [
    "click>=8.0",      # CLI framework
    "rich>=13.0",      # Terminal formatting
    "psycopg2-binary>=2.9",  # PostgreSQL driver
    # ... existing deps
]
```

---

## Files Changed

```
📝 Modified:
- src/cli/reviews.py (+533 lines)
- src/db/repositories/review_repository.py (+299 lines)
- src/reviews/service.py (+90 lines)
- src/pyproject.toml (+3 deps)

✨ Created:
- ARCHITECTURE.md (14KB)
- src/db/connection.py
- src/db/repositories/base.py
- src/reviews/models.py
- scripts/reviews.py
- docs/REVUE-91-dod.md

📊 Total: +1,347 lines, -24 lines
```

---

## Next Steps

1. **Merge this PR** → Makes CLI available for dev use
2. **REVUE-92:** Implement rating TUI to populate quality data
3. **REVUE-93:** Add auto-scoring heuristics
4. **REVUE-94:** Implement .revue.yml pattern support
5. _(Optional)_ Add unit/integration tests for reviews.py

---

## Breaking Changes

None — this is net-new functionality.

---

## Screenshots

### List Command (Table Output)
```
Reviews (2 total)                                
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Ticket ID  ┃ Branch      ┃ Model        ┃ Tier ┃ Findings ┃ Created          ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ REVUE-TEST │ test-branch │ test-model-3 │ free │        1 │ 2026-03-31 16:56 │
└────────────┴─────────────┴──────────────┴──────┴──────────┴──────────────────┘
```

### Show Command
```
Review: REVUE-TEST
Branch: test-branch
Model: test-model-3 (free)
Created: 2026-03-31 16:56
Findings: 1

               Findings               
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━┳━━━━━━━┓
┃ Severity ┃ Mode     ┃ File ┃ Issue ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━╇━━━━━━━┩
│ info     │ baseline │ x.py │ Test  │
└──────────┴──────────┴──────┴───────┘
```

### Suppression Trend
```
Context Suppression Trend                    
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Date       ┃ Ticket     ┃ Baseline ┃ Contextual ┃ Suppression ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 2026-03-31 │ REVUE-TEST │        1 │          1 │        0.0% │
└────────────┴────────────┴──────────┴────────────┴─────────────┘
```

---

## Reviewer Notes

- **Architecture:** Please review ARCHITECTURE.md for project standards
- **Repository pattern:** All SQL is in ReviewRepository, not scattered
- **Empty states:** Analytics queries work but show guidance until REVUE-92/93/94 data exists
- **Type safety:** Full type hints — mypy clean

---

## Definition of Done

✅ All acceptance criteria met  
✅ Architecture compliant (SOLID, repository pattern)  
✅ Graceful error handling  
✅ Documentation complete  
✅ Manual testing verified  

See `docs/REVUE-91-dod.md` for full checklist.

---

**Ready to merge.**

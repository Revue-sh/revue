# REVUE-106: Complete AIReviewer→revue migration

## User Story
As a developer, I want `src/AIReviewer/` to be fully absorbed into `src/revue/` and then deleted, so that there is a single canonical package with no legacy parallel structure and no code living outside the `revue` namespace.

## Background
REVUE-102 was closed as Done but the migration is incomplete. `src/AIReviewer/` still exists as a parallel package alongside `src/revue/` — the original engine living as a sibling, not absorbed into it.

Current repo structure (the problem):
```
src/
  AIReviewer/      ← legacy, should not exist
    agents/
    core/
    teams/
    tests/
  revue/           ← canonical
    agents/
    core/
    teams/
    tests/
```

Target structure:
```
src/
  revue/           ← single canonical package, everything here
    agents/
    core/
    teams/
    tests/
```

## Known Remaining Work (as of 2026-04-03)

| Location | Problem |
|---|---|
| `src/revue/core/pipeline.py` | 5 live `from AIReviewer.core.*` imports |
| `src/AIReviewer/tests/` | Not migrated to `revue/tests/` |
| `bitbucket-pipelines.yml` | Still references `AIReviewer/tests/` in CI |

## Acceptance Criteria
1. **AC1:** All imports in `src/revue/` that referenced `AIReviewer.core` are updated to `revue.core` equivalents
2. **AC2:** All test files from `src/AIReviewer/tests/` are migrated to `src/revue/tests/` with imports updated from `AIReviewer.core.*` to `revue.core.*`
3. **AC3:** No duplicate test coverage — if a test already exists in `revue/tests/` covering the same module, merge or reconcile rather than blindly copy
4. **AC4:** `bitbucket-pipelines.yml` updated to run only `revue/tests/` (`AIReviewer/tests/` reference removed)
5. **AC5:** `src/AIReviewer/` folder is deleted from the repository
6. **AC6:** Full test suite passes after deletion — `cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q` with zero failures
7. **AC7:** No reference to `AIReviewer` remains anywhere in `src/revue/`, CI config, or documentation

## Test Cases
1. **TC1-No-Import-Errors:** `python -c "from revue.core.pipeline import ReviewPipeline"` with `AIReviewer/` absent → no `ImportError` → AC1
2. **TC2-Tests-Pass:** `cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q` → all pass → AC6
3. **TC3-Tests-Migrated:** Every test file previously in `AIReviewer/tests/` has a counterpart in `revue/tests/` with `revue.core.*` imports → AC2
4. **TC4-No-Duplicates:** No two test functions in `revue/tests/` cover the exact same assertion for the same module → AC3
5. **TC5-CI-Config:** `bitbucket-pipelines.yml` contains no reference to `AIReviewer` → AC4
6. **TC6-Folder-Gone:** `ls src/AIReviewer` → no such directory → AC5
7. **TC7-No-References:** `grep -r "AIReviewer" src/revue/` → zero results → AC7

## Out of Scope
- Any feature changes — purely structural migration
- Renaming Python classes or functions from AIReviewer naming conventions (only the package namespace changes)

## Dependencies
- REVUE-102 (context — this story completes what REVUE-102 left unfinished)

## Implementation Approach

### Step 1 — Expose all remaining failures
```bash
mv src/AIReviewer src/_AIReviewer_bak
cd src && PYTHONPATH=$(pwd) python -c "from revue.core.pipeline import ReviewPipeline"
# Note every ImportError
```

### Step 2 — Fix pipeline.py imports
```python
# src/revue/core/pipeline.py
# Change all:
from AIReviewer.core.agent_loader   import load_all_agents
from AIReviewer.core.agent_runner   import run_agents_parallel
from AIReviewer.core.nova_consolidator import consolidate
from AIReviewer.core.shared_analysis import run_shared_analysis
from AIReviewer.core.cleo_router    import route
# To:
from revue.core.agent_loader        import load_all_agents
from revue.core.agent_runner        import run_agents_parallel
from revue.core.nova_consolidator   import consolidate
from revue.core.shared_analysis     import run_shared_analysis
from revue.core.cleo_router         import route
```

### Step 3 — Migrate tests
```bash
# For each test file in src/_AIReviewer_bak/tests/:
#   1. Copy to equivalent path in src/revue/tests/
#   2. Update imports: AIReviewer.core.* → revue.core.*
#   3. Check for existing counterpart in revue/tests/ — merge if duplicate
```

### Step 4 — Update CI
```yaml
# bitbucket-pipelines.yml
# Change:
cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ AIReviewer/tests/ -q
# To:
cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q
```

### Step 5 — Validate and delete
```bash
cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q  # must pass
grep -r "AIReviewer" src/revue/                                # must be zero
rm -rf src/_AIReviewer_bak                                     # final deletion
```

## Estimate
2-3 days

## Epic
REVUE-87: Developer Experience & Transparency

# Review Comparison: REVUE-86 — Add --pr-description-file flag
**Date:** 2026-03-31  
**Diff size:** ~272 lines, 4 files  
**Model:** claude-sonnet-4-5 (free tier — simplified path)  
**Status:** ⏳ Baseline pending (AI_API_KEY needed for local run)

---

## CI Pipeline Observations (proxy for comparison)

We don't have a local baseline run yet, but we have two CI pipeline runs
to compare directly:

### Run 1 — No PR description (PR #20, REVUE-83 branch)
- **94 findings** across 12 files
- **29 high/medium**
- Notable false positives:
  - `test_vcs_adapter.py` deletion flagged as "critical coverage loss" — false positive, coverage in `test_vcs_adapters.py` (plural)
  - `docs/session-continuation.md` accuracy issues — internal notes, not production code
  - Multiple findings on intentional architectural decisions (e.g. `_def.system_prompt` mutation)

### Run 2 — With PR description (PR #21, REVUE-86 branch)
- **26 findings** across 4 files  
- **6-7 high/medium**
- All findings were **actionable and fixed:**
  - Unused imports in tests
  - Fragile assertion logic
  - Missing empty-file validation
  - Module-level import improvements
- **Zero false positives** on intentional decisions

---

## Summary (CI-derived)

| Metric | No Context | With Context | Delta |
|--------|------------|--------------|-------|
| Total findings | 94 | 26 | -68 (-72%) |
| High/Medium | 29 | 6-7 | ~-22 |
| False positives (identified) | 3+ | 0 | -3 |
| Actionable findings fixed | partial | all 6-7 | ✅ |

---

## Key Observations

### Out of Scope section works
The PR description included `Out of Scope: Removing --auto-detect-pr`. Revue did not
flag the coexistence of both `--auto-detect-pr` and `--pr-description-file` in the CLI.
Without context, this would likely have triggered a "dead code" or "redundant flag" finding.

### Smaller diff = fewer findings (confound)
PR #21 was 4 files vs PR #20's 12 files. Some of the reduction is diff size, not just
context. **Action:** Run a proper controlled comparison — same diff, same model, with/without context.

### Free tier uses simplified path
Both runs used free tier (orchestrator + code-quality + consolidator only, simplified loop).
The full orchestration path with per-agent context filtering was not exercised.
**Action:** Test with pro tier override to see per-agent context routing effect.

---

## Actions

- [ ] Run local baseline once `AI_API_KEY` is available: `./scripts/run-comparison.sh REVUE-86 docs/review-comparisons/REVUE-86/pr_description.txt`
- [ ] Add `test_vcs_adapter.py` deletion pattern to PR template Out of Scope guidance
- [ ] Consider adding a `.revue.yml` note about `test_vcs_adapters.py` coverage for the original `vcs_adapter.py`
- [ ] First pro-tier comparison run will be much more informative (per-agent filtering active)

---

## PR Description Used

```
## Summary
Adds --pr-description-file so the CLI accepts a pre-fetched PR description
from any CI platform without embedding platform logic in Python.

## Out of Scope
Removing --auto-detect-pr. API fetch inside CLI.

## Dependencies
REVUE-84 merged.
```

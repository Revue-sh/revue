# REVUE-93 — Auto-Heuristic Quality Scorer for Findings

**Status:** ready-for-dev
**Epic:** REVUE-87
**Estimate:** 3 points
**Priority:** Medium

---

## User Story

As a developer, I want an automatic quality scorer that estimates clarity and actionability from finding text, so that unrated findings still contribute to trend analysis.

## Background

REVUE-92 introduced the human rating flow for manually scoring findings on clarity and actionability. However, most findings will never receive a human rating — the volume is too high and the process is manual. Without auto-scoring, trend dashboards will exclude the majority of findings, skewing analytics. This story adds a heuristic scorer that runs automatically at import time, inserting `rated_by = auto` quality rows for every finding. Human ratings always take precedence at read-time, so auto-scores serve as a useful baseline that can be overridden without conflict.

## Acceptance Criteria

**AC1 — Scorer runs at import time:**
The auto-scorer is called inside `import_comparison()` in `src/db/import_review.py`, after findings are inserted and before `conn.commit()`. For each finding imported, the scorer inserts two rows into `finding_quality`: one for the `clarity` dimension and one for the `actionability` dimension. Both rows use `rated_by_id` referencing the `auto` entry in `rating_sources`.

**AC2 — Correct heuristic scoring:**
Clarity score (1–5) is computed as the sum of:
- `issue` AND `details` fields both non-empty (+2)
- `len(issue) > 20` (+1)
- No vague words (`consider`, `might`, `perhaps`) in issue+details (+1)
- Contains a specific file path or line reference (+1)

Actionability score (1–5) is computed as the sum of:
- `recommendation` field non-empty (+2)
- Contains a code snippet or file path (+1)
- Uses a specific action verb (`add`, `remove`, `change`, `replace`) in recommendation (+1)
- Mentions an exact change needed (e.g., file path + verb in same sentence) (+1)

Minimum score is 1 (not 0) — a finding that matches no heuristics still gets score 1.

**AC3 — Human override convention:**
At write-time, auto-scorer inserts freely — the UNIQUE constraint `(finding_id, dimension_id, rated_by_id)` prevents duplicates for the same source. At read-time, queries MUST prefer `rated_by_id = human` over `rated_by_id = auto` when both exist. This is a read-convention, not a write-constraint. The auto-scorer does not check for or skip findings that already have human ratings.

**AC4 — Accuracy benchmarked against synthetic ground truth:**
A fixture of 5 manually-scored findings is embedded in the test suite as ground truth. The auto-scorer's output for each finding must be within ±1 of the expected score on both dimensions. (Note: REVUE-86 human ratings are not available in the database — the baseline run was never completed — so this synthetic benchmark replaces the original "benchmarked against REVUE-86 human ratings" criterion.)

**AC5 — Idempotent inserts:**
Re-running the scorer on the same finding does not create duplicate rows. The `ON CONFLICT (finding_id, dimension_id, rated_by_id) DO NOTHING` clause ensures idempotency.

## Test Cases

**test_scorer_inserts_two_rows_per_finding** (AC1):
- Insert a finding into the DB.
- Call `score_findings()` with that finding's data.
- Assert exactly two rows in `finding_quality` for the finding: one `clarity`, one `actionability`.
- Assert both rows have `rated_by_id` pointing to `auto` in `rating_sources`.

**test_clarity_heuristic_scoring** (AC2):
- Construct findings with known field values (e.g., issue > 20 chars, no vague words, file reference present).
- Call clarity scorer.
- Assert score matches expected heuristic sum (clamped to 1–5).

**test_actionability_heuristic_scoring** (AC2):
- Construct findings with known recommendation, code snippet, action verbs.
- Call actionability scorer.
- Assert score matches expected heuristic sum (clamped to 1–5).

**test_minimum_score_is_one** (AC2):
- Construct a finding with empty issue, details, recommendation.
- Assert both clarity and actionability scores are 1, not 0.

**test_human_override_read_convention** (AC3):
- Insert a finding with both auto and human quality rows for the same dimension.
- Query using the expected read-time convention (`ORDER BY rated_by_id` preferring human, or `DISTINCT ON` + priority).
- Assert the human score is returned, not the auto score.

**test_synthetic_benchmark_accuracy** (AC4):
- Load the 5 ground-truth fixture findings.
- Run the auto-scorer on each.
- Assert every score is within ±1 of the expected ground-truth value.

**test_idempotent_inserts** (AC5):
- Insert a finding and call `score_findings()` twice.
- Assert still exactly two rows (one per dimension) — no duplicates.

**test_integration_with_import_comparison** (AC1):
- Run `import_comparison()` end-to-end on a test fixture directory.
- Assert `finding_quality` rows exist for every imported finding with `rated_by_id = auto`.

## Out of Scope

- Machine-learned scoring models — this story uses only deterministic string heuristics.
- Updating or recalculating scores when findings are edited post-import.
- Read-side query helpers or views for the human-override convention (AC3 documents the convention; implementing a reusable query/view is a separate story).
- Scoring dimensions beyond clarity and actionability.
- UI or CLI output of auto-scores (analytics dashboards are a downstream story).
- Modifications to the `finding_quality` schema — the existing schema from REVUE-89 is used as-is.

## Dependencies

| Dependency | Status | Resolution |
|---|---|---|
| REVUE-92 (Human Rating Flow) | Done ✅ | Confirms `finding_quality` table, `rating_sources` seed data, and dimension IDs are in place. |
| REVUE-90 (Review Import Pipeline) | Done ✅ | Provides `import_comparison()` call site in `src/db/import_review.py`. |
| REVUE-89 (Knowledge Base Schema) | Done ✅ | Schema with `finding_quality`, `quality_dimensions`, `rating_sources` tables and seed data. |

No unresolved blockers.

## Dev Notes

**Call site integration:**
In `src/db/import_review.py`, function `import_comparison()` (line ~459), the scorer must be called after findings are inserted by `import_review()` and before `conn.commit()` on line ~525. The scorer receives the cursor (same transaction) and the list of finding IDs + finding data just inserted.

**Schema details:**
- `finding_quality` table: `(id, finding_id, dimension_id, score, rated_by_id, rated_at)`
- `quality_dimensions` seed: `clarity` (id=1), `actionability` (id=2) — look up by name, don't hardcode IDs.
- `rating_sources` seed: `human`, `auto` — look up by name.
- UNIQUE constraint: `(finding_id, dimension_id, rated_by_id)` — use `ON CONFLICT DO NOTHING` for idempotency.

**New module:**
Create `src/db/auto_scorer.py`. Keep it focused: a `score_finding()` function for a single finding and a `score_findings()` batch function. Pure Python string operations only — no new external dependencies.

**Heuristic implementation hints:**
- Vague words list: `["consider", "might", "perhaps", "maybe", "possibly", "could"]` — match as whole words, case-insensitive.
- Action verbs list: `["add", "remove", "change", "replace", "rename", "delete", "move", "update", "refactor", "extract"]` — match as whole words in recommendation text.
- "Contains file path": check for `/` in a token or common extensions (`.py`, `.js`, `.ts`, etc.).
- "Mentions exact change needed": file path AND action verb in recommendation text.
- Score clamping: `max(1, min(5, raw_score))`.

**DB container requirement:**
The `revue-db` Postgres container must be running (Rancher Desktop). Connection via `DATABASE_URL` env var from `~/.zshenv`.

**No new dependencies:**
Pure Python string operations only. No NLP libraries, no regex beyond `re` stdlib.

**Read-time convention for AC3:**
Document in a code comment that read-side queries should use something like:
```sql
SELECT DISTINCT ON (fq.finding_id, fq.dimension_id)
    fq.*
FROM finding_quality fq
JOIN rating_sources rs ON rs.id = fq.rated_by_id
ORDER BY fq.finding_id, fq.dimension_id,
         CASE rs.name WHEN 'human' THEN 0 ELSE 1 END,
         fq.rated_at DESC;
```
This is informational — implementing a view or helper is out of scope for this story.

## Tasks/Subtasks

- [ ] Task 1: Create `src/db/auto_scorer.py` module
  - [ ] Implement `compute_clarity_score(finding: dict) -> int` heuristic
  - [ ] Implement `compute_actionability_score(finding: dict) -> int` heuristic
  - [ ] Implement `score_finding(cursor, finding_id: int, finding: dict)` — inserts two `finding_quality` rows
  - [ ] Implement `score_findings(cursor, findings: list[tuple[int, dict]])` — batch wrapper
  - [ ] Add `ON CONFLICT DO NOTHING` for idempotency
  - [ ] Look up `dimension_id` and `rated_by_id` by name, not hardcoded IDs
- [ ] Task 2: Integrate into `import_review.py`
  - [ ] Import `score_findings` from `auto_scorer`
  - [ ] Call scorer after findings inserted, before `conn.commit()`, passing cursor + finding IDs/data
  - [ ] Collect `(finding_id, finding_dict)` tuples during insert loop to pass to scorer
- [ ] Task 3: Create synthetic ground-truth fixture
  - [ ] Define 5 findings with hand-scored clarity and actionability values
  - [ ] Store as a pytest fixture or JSON file in `tests/fixtures/`
- [ ] Task 4: Write unit tests for heuristic functions
  - [ ] `test_clarity_heuristic_scoring`
  - [ ] `test_actionability_heuristic_scoring`
  - [ ] `test_minimum_score_is_one`
- [ ] Task 5: Write integration tests (require DB)
  - [ ] `test_scorer_inserts_two_rows_per_finding`
  - [ ] `test_idempotent_inserts`
  - [ ] `test_human_override_read_convention`
  - [ ] `test_synthetic_benchmark_accuracy`
  - [ ] `test_integration_with_import_comparison`
- [ ] Task 6: Document read-time human-override convention
  - [ ] Add SQL comment in `auto_scorer.py` with example query

---

## Dev Agent Record

### Implementation Plan
_(to be filled by dev agent)_

### Debug Log
_(to be filled by dev agent)_

### Completion Notes
_(to be filled by dev agent)_

## File List
_(to be filled by dev agent)_

## Change Log
_(to be filled by dev agent)_

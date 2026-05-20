# Story: REVUE-112 Phase 2 — Classify/Respond Split & Pipeline Ordering Fix

**Status:** ready-for-dev
**Jira:** REVUE-112 (In Progress)
**Epic:** REVUE-E8 — Institutional Memory & Pattern Enforcement
**Sprint:** current
**Story Points:** 3

---

## Story

As Revue's pipeline, I need to classify developer replies **before** running agents so that newly allowed/disallowed patterns are applied in the current review cycle, not the next one.

**Background:** Phase 1 (PR #43) shipped `WontFixReplyService.process_wont_fix_replies()` but it runs *after* agents. This means a developer's "won't fix" decision is recorded but agents still generate the finding redundantly. The classify/respond split fixes this by making classification a zero-side-effect query that happens before agents, and relegating all I/O (lessons PR, replies, thread resolution) to a respond phase after comment posting.

**Phase 1 already done (do not re-implement):**
- `BitbucketAdapter.resolve_comment`, `get_comment_replies`, `post_reply` fixes
- `PRContext` dataclass in `core/models.py`
- `WontFixReplyService` + `process_wont_fix_replies()` in `comments/service.py`
- `NovaConsolidator.analyse_reply_threads()` in `core/dedup_consolidator.py`
- `cli.py` `resolved_pr_id` unconditional assignment fix (commit `d2676ab`)
- Tests TC1a–TC17 (741/741 passing)
- SOLID clause in `docs/story-dod-checklist.md`

---

## Acceptance Criteria

- **AC14** — `process_wont_fix_replies` split into `classify(pr_number) -> ClassificationResult` (zero side-effects) and `respond(result, pr_number)` (all I/O). `process_wont_fix_replies` retained as thin wrapper.
- **AC15** — `ClassificationResult` dataclass added to `src/revue/core/models.py`. Fields: `patterns_to_allow: list[dict]`, `patterns_to_disallow: list[dict]`, `state_updates: list[dict]`, `decisions: list[dict]`. Must NOT live in `comments/service.py` (layered architecture: core must not import from comments).
- **AC16** — `pipeline.run()` reordered: classify phase runs before diff parsing and before agent execution. Respond phase runs after comment posting (end of run).
- **AC17** — After `classify()` returns, pipeline patches in-memory config before `_run_orchestration` or `_run_simplified`: `self.config.allowed_patterns += result.patterns_to_allow` and `self.config.disallowed_patterns += result.patterns_to_disallow`. No file write to disk. `.revue.yml` on working tree is not touched by the classify phase.
- **AC18** — `state_updates` from `ClassificationResult` applied via per-fingerprint `mark_resolved` calls in a loop, after config patching and before diff parsing.
- **AC19** — Pattern injection in `_run_orchestration` reads the already-patched `self.config`. No changes to `inject_patterns` needed — ordering fix alone satisfies this.
- **AC20** — Bitbucket-only platform guard (`pr_context.platform == "bitbucket"`) unchanged. No other platforms touched.
- **AC21** — `classify()` is provably side-effect free: any file write, API POST, or store mutation in the classify path is a bug. Must be documented in docstring.
- **AC22** — All existing tests (TC1a–TC17) pass without modification. New tests TC18–TC26 added.

---

## Tasks/Subtasks

### Task 1: Add `ClassificationResult` to `core/models.py`
- [ ] T1.1 — Write failing test: `ClassificationResult` import + field existence (RED)
- [ ] T1.2 — Add `ClassificationResult` dataclass to `src/revue/core/models.py` (GREEN)
- [ ] T1.3 — Run full test suite: 741 existing pass, new test passes

### Task 2: Add `classify()` to `WontFixReplyService`
- [ ] T2.1 — Write failing tests TC18, TC19, TC20 (RED)
  - TC18: `test_classify_returns_classification_result` — two threads, one allowed_pattern, one reason_missing → `ClassificationResult` with `patterns_to_allow` len 1, `state_updates` len 1, `decisions` len 2
  - TC19: `test_classify_performs_no_writes` — mock adapter + AI client; `_append_pattern_to_config`, `post_reply`, `_ensure_lessons_pr` never called
  - TC20: `test_classify_empty_threads_returns_empty_result` — adapter returns no threads → empty ClassificationResult, AI client NOT called
- [ ] T2.2 — Implement `classify(pr_number: int) -> ClassificationResult` (GREEN): extract steps 1–2 from `process_wont_fix_replies`; zero side-effects; docstring AC21 wording
- [ ] T2.3 — Run tests: TC18, TC19, TC20 pass; TC1a–TC17 still pass

### Task 3: Add `respond()` to `WontFixReplyService` + thin wrapper
- [ ] T3.1 — Write failing tests TC21, TC22 (RED)
  - TC21: `test_respond_posts_replies_and_creates_lessons_pr` — given a ClassificationResult with one allowed decision, respond calls `post_reply` and `_ensure_lessons_pr`
  - TC22: `test_process_wont_fix_replies_is_wrapper` — verify `process_wont_fix_replies` calls `classify` then `respond` in sequence
- [ ] T3.2 — Implement `respond(result: ClassificationResult, pr_number: int) -> None` (GREEN): extract step 3 from `process_wont_fix_replies`; update wrapper
- [ ] T3.3 — Run tests: TC21, TC22 pass; full suite still passes

### Task 4: Reorder `pipeline.run()` + in-memory config patching
- [ ] T4.1 — Write failing tests TC23–TC26 (RED)
  - TC23: `test_pipeline_classify_runs_before_agents` — mock WontFixReplyService; verify `classify` called before `_run_orchestration`
  - TC24: `test_pipeline_config_patched_after_classify` — after classify returns patterns, `self.config.allowed_patterns` includes them before agents run
  - TC25: `test_pipeline_state_updates_applied_before_diff_parse` — `mark_resolved` called before `parse_diff_file`
  - TC26: `test_pipeline_respond_runs_after_comment_posting` — mock CommentService and respond; verify order
- [ ] T4.2 — Reorder `pipeline.run()` (GREEN):
  1. classify phase (before diff parse)
  2. patch `self.config` in-memory
  3. apply `state_updates`
  4. diff parse + filter (existing Step 1)
  5. agents (existing Step 2)
  6. consolidation + verdict (existing Steps 3–4)
  7. respond phase (after comment posting in `cli.py` or at end of `run()`)
- [ ] T4.3 — Run full test suite; all 741 + 9 new tests pass

### Task 5: Commit pending changes from Phase 1
- [ ] T5.1 — Commit `💬` emoji + `✅` closure message + `usage_tracker.py` TODO

---

## Dev Notes

### Architecture constraints (MUST follow)
- `ClassificationResult` lives in `core/models.py` — `core` must never import from `comments`
- Pipeline imports `WontFixReplyService` lazily (already done in Phase 1) to avoid circular imports
- `classify()` must be a query (no side-effects). If a test catches a write, it is a bug — fix immediately
- `self.config` mutation is in-memory only. Never call `config.save()` or write `.revue.yml` from the classify path

### Where to find Phase 1 code
- `WontFixReplyService.process_wont_fix_replies` → `src/revue/comments/service.py:390`
- Pipeline reply tracking → `src/revue/core/pipeline.py:497` (`_run_wont_fix_reply_tracking`)
- `PRContext` dataclass → `src/revue/core/models.py`
- Thread analysis → `src/revue/core/dedup_consolidator.py`

### Respond-phase placement
The respond phase (lessons PR, thread replies, thread resolution) runs at the END of `pipeline.run()` — after the usage tracking fire-and-forget, after the findings phase, and after any comment posting done by `cli.py`. The `cli.py` calls `CommentService.post_comments()` *after* `pipeline.run()` returns, so `respond` must also run in `cli.py` after `post_comments`, OR at the end of `pipeline.run()` before returning results (current `_run_wont_fix_reply_tracking` position is correct for this).

The key architectural change is: **classify moves to the TOP of `pipeline.run()`, respond stays at the BOTTOM.**

### Uncommitted changes (commit first — T5.1)
- `src/revue/cli.py`: `print("[revue] ✅ Review cycle complete.")` before `return 0`
- `src/revue/core/pipeline.py`: `💬` emoji on reply tracking log lines
- `src/revue/core/usage_tracker.py`: TODO comment about api.revue.sh NXDOMAIN

---

## Dev Agent Record

### Implementation Plan
_To be filled during implementation._

### Completion Notes
_To be filled on completion._

### Debug Log
_To be filled if issues arise._

---

## File List

_Files changed in this story (to be updated as work progresses):_

- `src/revue/core/models.py` — add `ClassificationResult`
- `src/revue/comments/service.py` — add `classify()`, `respond()`, update wrapper
- `src/revue/core/pipeline.py` — reorder + config patching + `💬` emoji
- `src/revue/cli.py` — `✅` closure message
- `src/revue/core/usage_tracker.py` — TODO comment
- `src/revue/tests/core/test_models.py` — T1 tests
- `src/revue/tests/comments/test_service.py` — TC18–TC22
- `src/revue/tests/core/test_pipeline.py` — TC23–TC26

---

## Change Log

| Date | Change |
|------|--------|
| 2026-04-10 | Story file created for Phase 2 |

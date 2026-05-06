# Comment Posting Refactor — Track 1 Delivery Plan

**Status:** Active
**Created:** 2026-05-02
**Retires:** Once PR 4 (or PR 5) merges to main
**Architecture reference:** `docs/architecture/comment-posting.md`

This document is a one-time migration roadmap. It captures the PR sequence, Jira ticket structure, and sequencing rationale for delivering the Track 1 typed pipeline. It is not durable architecture; once Track 1 ships, it is superseded by the architecture document.

---

## Jira Epic Structure

| Ticket | Title | PR |
|--------|-------|----|
| REVUE-208 | Comment posting contracts (models + stub modules) | PR 1 |
| REVUE-209 | Migrate body building to `comments/body_builder.py` | PR 2 |
| REVUE-210 | Migrate consolidation to `comments/consolidator.py` | PR 3 |
| REVUE-211 | Migrate posting to `comments/poster.py` | PR 4 |
| *(optional)* | `cli.py` dead code cleanup | PR 5 |

Dependencies: REVUE-209, REVUE-210, and REVUE-211 (PR 4) are all blocked by REVUE-208. REVUE-209, REVUE-210, and REVUE-211 can proceed in parallel once REVUE-208 merges (they each touch different modules and `cli.py` call sites).

---

## PR 1 — Contracts (~200 LOC, behaviour-neutral)

**Jira:** REVUE-208
**Blocking:** All subsequent migration PRs

### What ships

- `src/revue/comments/models.py` — typed data contracts:
  - `AgentFinding` dataclass
  - `SynthesisGroup` dataclass
  - `ConsolidatedFinding` dataclass (with `attribution` as a required field)
  - `GroupingStrategy` Protocol
  - `SynthesisStrategy` Protocol
  - `FindingPostProcessor` Protocol
- `src/revue/comments/consolidator.py` — empty stub (importable, not yet wired)
- `src/revue/comments/body_builder.py` — empty stub (importable, not yet wired)
- `src/revue/comments/poster.py` — empty stub (importable, not yet wired)

### What does NOT ship

- No `cli.py` changes. All existing tests pass unmodified. The stubs are imported but not called by any production path.

### Why this PR exists

This PR is the trellis. It is small enough to review carefully in one sitting. Reviewers pin the type shapes, the Protocol signatures, and the field names *before* any migration work happens — so migration PRs argue only about behaviour, not about what the types should have been. It also unblocks REVUE-209, REVUE-210, and REVUE-211 to proceed in parallel if capacity allows.

---

## PR 2 — Migrate body building

**Jira:** REVUE-209
**Blocked by:** REVUE-208 (PR 1)

### What ships

- `comments/body_builder.py` fully implemented:
  - Single-finding renderer (prose + optional suggestion block)
  - Multi-finding grouped renderer (prose for each finding, attribution headers, unified suggestion block)
  - Summary comment renderer (reads `summary_sink` for unanchored findings section)
  - Per-platform kind-switching (GitHub suggestion fences vs GitLab suggestion fences vs Bitbucket inline format)
- `cli.py` updated to call `BodyBuilder` instead of inline rendering logic
- `_build_merged_comment_body` private method deleted from `cli.py`

### Regression fix delivered

**C3 regression #1:** Multi-finding grouped comments restore agent attribution.

---

## PR 3 — Migrate consolidation

**Jira:** REVUE-210
**Blocked by:** REVUE-208 (PR 1); can run in parallel with PR 2

### What ships

- `comments/consolidator.py` fully implemented:
  - `Consolidator` class with `GroupingStrategy`, `SynthesisStrategy`, `FindingPostProcessor` injection
  - `ProximityAndCountGroupingStrategy(n=3, k=3)` — default Pass A implementation
  - `NovaSingleShotStrategy` — wraps migrated `dedup_consolidator.py` logic, extended to handle proximity groups, with deterministic-concatenation fallback on Nova failure
  - `NoOpSuggestionDropper` — no-op `code_replacement` detection
  - `UnanchoredFindingExtractor` — demotes unanchored findings to `summary_sink`
- `core/dedup_consolidator.py` deleted (logic now lives in `NovaSingleShotStrategy`)
- `cli.py` updated to call `Consolidator` instead of the old `dedup_consolidator` path
- `.revue.yml` schema extended with `consolidation:` stanza

### Regression fixes delivered

**C3 regressions #2 and #3:** Grouped comments restore `code_replacement`; proximity bound prevents 8-finding collapse. No-op suggestion bug resolved.

---

## REVUE-199 — Nova synthesis quality (companion to PR 3)

**Jira:** REVUE-199
**Depends on:** REVUE-210 (PR 3)
**Must complete before:** Track 1 is considered done

### What ships

- Nova's synthesis prompt loads the full `nova.yaml` system prompt instead of the hardcoded 2-line inline string
- `AIReview` gains a `language` field populated from `FileChange.language`; `_build_synthesis_prompt` uses it directly
- **Deferred from REVUE-210:** `NovaSingleShotStrategy` prompt extended so Nova produces:
  - A deterministic per-agent attribution block (`[Leo] ...`, `[Titan] ...`) — always visible to the developer
  - A single unified `code_replacement` block addressing all findings in the group at once
  - A brief explanation narrative connecting the findings and describing how the fix addresses them

### Why it belongs here

REVUE-210 code review (2026-05-04) surfaced that the current Nova synthesis replaces individual findings with prose, stripping agent attribution from the developer's view. The correct UX is attribution first (deterministic, always visible), Nova second (adds explanation + unified code block). The prompt changes to implement this belong in REVUE-199 alongside the existing prompt quality work.

### Additional agent prompt constraints (from REVUE-210 dogfood, 2026-05-04)

Two comment quality regressions observed in live dogfood review:

1. **`suggestion` field must be prose only** — agents are embedding inline code examples in the suggestion text (e.g. `issues = [...]; issue_str = '\n'.join(issues)`). Code belongs exclusively in `code_replacement`. The agent system prompt must explicitly prohibit code examples in `suggestion`.
2. **`code_replacement` must be complete and consistent with `suggestion`** — observed a case where the Action prose proposed `issue_str = '\n'.join(issues)` but the replacement block stopped at building the list and omitted the join, making the replacement non-functional. The agent prompt must require that `code_replacement` is a complete, working replacement or omitted entirely.

---

## PR 4 — Migrate posting

**Jira:** REVUE-211
**Blocked by:** REVUE-208 (PR 1); best sequenced after PR 2 and PR 3 are merged

### What ships

- `comments/poster.py` fully implemented:
  - Position resolution (line → diff position mapping, currently inline in `cli.py`)
  - `VCSAdapter` call (post / update comment)
  - Deduplication against existing comments (fingerprint-based, prevents re-posting on re-run)
- `cli.py` reduced to orchestration only: CLI argument parsing + pipeline wiring + `Poster` call
- Target `cli.py` size: ~400–500 lines

---

## PR 5 (Optional) — `cli.py` dead code cleanup

**Jira:** Create if needed after PR 4 review surfaces residual dead paths

If the migration leaves dead private methods, unused imports, or orphaned branches in `cli.py`, a final cleanup PR removes them. This PR is excluded from the MVP critical path — the system is correct after PR 4; PR 5 is housekeeping.

---

## Sequencing Rationale

Each PR is independently reviewable and reversible. The contracts PR (PR 1) pins the types before any migration happens — reviewers spend their attention on behaviour in PRs 2–4, not on re-arguing types. Total time to MVP is comparable to a big-bang refactor but with much lower review risk and a trivial rollback path (any PR that regresses can be reverted without affecting the others).

```
PR 1 (contracts)
  ├─► PR 2 (body builder) ─┐
  ├─► PR 3 (consolidation) ─┤─► PR 4 (poster) ─► [optional PR 5]
  └─► PR 4 can start once PR 1 merges, but full wiring needs PR 2 + PR 3
```

---

## Definition of Done (Track 1)

- All existing tests pass after each PR
- New unit tests cover: each `GroupingStrategy` permutation (boundary N/K values); `NovaSingleShotStrategy` fallback path; `NoOpSuggestionDropper` with known no-op fixture; `UnanchoredFindingExtractor` output to `summary_sink`; `BodyBuilder` per-platform rendering for all comment shapes
- MR !22 scenarios regression-checked: attribution visible on grouped comments; `code_replacement` present when underlying finding carried one; 8-finding `line_resolver.py` collapse does not recur
- `cli.py` contains no rendering or consolidation logic (only orchestration)
- `docs/guides/revue-yml-reference.md` updated with `consolidation:` stanza

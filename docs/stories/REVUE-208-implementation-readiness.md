---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments:
  - docs/architecture/comment-posting.md
  - docs/planning/comment-posting-refactor-plan.md
  - _bmad-output/planning-artifacts/ux-design-specification.md
  - docs/planning/prd.md
ticket: REVUE-208
status: done
---

# Implementation Readiness Assessment Report

**Date:** 2026-05-02
**Project:** revue.io
**Ticket:** REVUE-208 — Comment posting contracts (models and stub modules)

---

## Step 1 — Document Discovery

### Document Inventory

| Document | Type | Path | Status |
|----------|------|------|--------|
| REVUE-208 Jira ticket | Ticket | Jira (urukia.atlassian.net) | ✅ Found |
| Comment Posting Architecture | Architecture | `docs/architecture/comment-posting.md` | ✅ Found |
| Track 1 Delivery Plan | Planning | `docs/planning/comment-posting-refactor-plan.md` | ✅ Found |
| UX Design Specification | UX | `_bmad-output/planning-artifacts/ux-design-specification.md` | ✅ Found (general) |
| Product Requirements Document | PRD | `docs/planning/prd.md` | ✅ Found (global) |

### No Duplicates Found

All documents are singular and non-conflicting. Architecture and delivery plan are complementary artefacts, not competing versions.

### Context Note

REVUE-208 is a **Task** (PR 1 of 5), not a standalone Epic. It introduces typed contracts only. PRs 2–4 (REVUE-209/213/214) are blocked by this ticket and deliver the actual implementation. No REVUE-208-specific Epic doc exists; the architecture and planning documents together fully specify the story.

---

## Step 2 — PRD Analysis

*PRD scoped to comment-posting layer requirements relevant to REVUE-208.*

### Functional Requirements Extracted

| ID | PRD Section | Requirement |
|----|-------------|-------------|
| FR1 | §3.2 | Inline review comments with severity levels (P0 MVP) |
| FR2 | §3.2 | Platform-native 1-click fix suggestions via Sage (P0 MVP) |
| FR3 | §4.3 | Nova merges, deduplicates, and prioritises findings from multiple agents |
| FR4 | §4.3 | Agent attribution preserved through the full consolidation pipeline |
| FR5 | §4.3/10.1 | Each finding carries: severity, file/line location, description, remediation |
| FR6 | §5.1 | Per-platform comment position model (GitHub: diff-position offset; GitLab: line_code hash) |
| FR7 | §10.1 | Inline comment format: severity badge + agent attribution + remediation block |
| FR8 | §10.1 | Platform-native suggestion block (code fix, 1-click applicable) |
| FR9 | §10.2 | PR-level summary comment: findings grouped by severity + strengths + Sage suggestions |
| FR10 | §13 | Graceful degradation — if agent fails, Nova proceeds with available findings |
| FR11 | §13 | Failing agent contribution marked as unavailable in the summary |

**Total FRs extracted (comment-posting scope): 11**

### Non-Functional Requirements Extracted

| ID | PRD Section | Requirement |
|----|-------------|-------------|
| NFR1 | §13 | Review completes within 3 minutes for diffs up to 2,000 changed lines |
| NFR2 | §13 | Source code and diffs never stored by Revue's cloud backend |
| NFR3 | §3.4 | False positive rate < 15% at MVP |
| NFR4 | §13 | Monorepo support — path-scoped configuration |
| NFR5 | §4.2 | Webhook signature verification before processing |

**Total NFRs extracted (comment-posting scope): 5**

### Additional Requirements / Constraints

- `ConsolidatedFinding` must be the typed handshake between Consolidator and BodyBuilder — raw dicts across this boundary are prohibited (architecture Decision 1)
- `attribution` is a **required** field — not optional — to make the regressions from MR !22 structurally impossible (architecture Decision 1)
- `.revue.yml` `consolidation:` stanza must be documented in `docs/guides/revue-yml-reference.md` (architecture Decision 2)
- All three Protocol interfaces (`GroupingStrategy`, `SynthesisStrategy`, `FindingPostProcessor`) must live in `comments/models.py` alongside data types (architecture Decision 4)

---

## Step 3 — Epic Coverage Validation

*Epic coverage derived from the Jira ticket structure (REVUE-208/212/213/214) and the Track 1 delivery plan, as no separate BMad Epic document exists for this initiative.*

### FR Coverage Matrix

| FR | PRD Requirement (summary) | Ticket / Module | Status |
|----|--------------------------|-----------------|--------|
| FR1 | Inline comments with severity | REVUE-208 (`ConsolidatedFinding.severity`) + REVUE-209 (`body_builder`) | ✅ Covered |
| FR2 | 1-click fix suggestions | REVUE-208 (`ConsolidatedFinding.code_replacement`) + REVUE-209 (`body_builder`) | ✅ Covered |
| FR3 | Nova merge/dedup/prioritise | REVUE-210 (`consolidator.py`: `NovaSingleShotStrategy`) | ✅ Covered |
| FR4 | Attribution preserved | REVUE-208 (`attribution` required field) — Decision 1 | ✅ Covered |
| FR5 | Finding fields: severity, location, description, remediation | REVUE-208 (`AgentFinding`, `ConsolidatedFinding` dataclasses) | ✅ Covered |
| FR6 | Per-platform position model | REVUE-211 (`poster.py`: VCSAdapter call + position resolution) | ✅ Covered |
| FR7 | Comment format: severity badge + attribution + remediation | REVUE-209 (`body_builder`: per-platform kind-switching) | ✅ Covered |
| FR8 | Platform-native suggestion block | REVUE-208 (`code_replacement` field) + REVUE-209 (`body_builder`) | ✅ Covered |
| FR9 | PR-level summary comment | REVUE-209 (`body_builder`: summary renderer + `summary_sink`) | ✅ Covered |
| FR10 | Graceful degradation on agent failure | REVUE-210 (`NovaSingleShotStrategy`: deterministic-concatenation fallback) | ✅ Covered |
| FR11 | Mark failing agent contribution unavailable | REVUE-209 (`body_builder`: unanchored section via `UnanchoredFindingExtractor`) | ✅ Covered |

### Missing Requirements

**None.** All 11 comment-posting FRs have a traceable implementation ticket and module.

### Coverage Statistics

- Total PRD FRs (comment-posting scope): 11
- FRs covered in ticket structure: 11
- **Coverage: 100%**

### Note on REVUE-208 Scope

REVUE-208 directly covers FR1 (partial — data types only), FR2 (partial — `code_replacement` field), FR4, and FR5. The remaining FRs are covered by downstream tickets (REVUE-209/213/214), which are explicitly blocked by REVUE-208 and cannot start until PR 1 merges. This sequencing is correct and intentional.

---

## Step 4 — UX Alignment Assessment

### UX Document Status

**Found:** `_bmad-output/planning-artifacts/ux-design-specification.md` (completed 2026-04-25, based on `product-brief.md` and `prd.md`).

### UX ↔ PRD Alignment

| UX Decision | PRD Requirement | Alignment |
|-------------|----------------|-----------|
| D1 — Severity-first visual hierarchy | FR1: severity levels on inline comments | ✅ Aligned |
| D2 — Agent attribution per finding | FR4: attribution preserved through consolidation | ✅ Aligned |
| D3 — Brand footer on all inline comments | FR7: comment format requirements | ✅ Aligned |
| 1-click suggestion uniformity (β option) | FR2: platform-native 1-click fix suggestions | ✅ Aligned |
| Unanchored findings → PR-level summary | FR9: PR-level summary comment | ✅ Aligned |
| Multi-finding comment: compact, attribution per item | FR7 + FR4 combined | ✅ Aligned |

### UX ↔ Architecture Alignment

| UX Requirement | Architecture Support | Alignment |
|----------------|---------------------|-----------|
| Severity badge as primary anchor | `ConsolidatedFinding` must carry severity (required field implied) | ✅ Supported |
| Attribution as credibility signal | `attribution` is **required** field in `ConsolidatedFinding` (Decision 1) | ✅ Supported |
| Unified one-click suggestion per grouped comment | `code_replacement` in `ConsolidatedFinding`; `NovaSingleShotStrategy` produces unified replacement (Decision 3) | ✅ Supported |
| Scan → read → act information density | Per-platform kind-switching in `BodyBuilder`; prose-only fallback when `code_replacement=None` | ✅ Supported |
| Unanchored findings demoted to summary | `UnanchoredFindingExtractor` + `summary_sink` injection (Decision 6) | ✅ Supported |

### Alignment Issues

None identified. The architecture decisions (particularly Decision 1 requiring `attribution` as a required field and Decision 3 choosing β over α) were explicitly designed to satisfy the UX requirements from the design session.

### Warnings

⚠️ **Minor:** The UX spec was produced before the architecture doc was finalised (UX: 2026-04-25; Architecture: 2026-05-02). Both documents are aligned, but the UX spec does not reference the architecture doc's decision numbers. This is cosmetic — no functional gap exists.

---

## Step 5 — Epic Quality Review

*Standards applied: create-epics-and-stories best practices. Brownfield refactor context acknowledged.*

### A. User Value Focus

| Ticket | Title | Framing | Assessment |
|--------|-------|---------|------------|
| REVUE-208 | Comment posting contracts — models and stub modules | Technical | ⚠️ Technical title, but user story body correctly states internal developer value |
| REVUE-209 | Migrate body building to `body_builder.py` | Technical | ⚠️ Technical title — user value is implicit (regression fixes C3 #1) |
| REVUE-210 | Migrate consolidation to `consolidator.py` | Technical | ⚠️ Technical title — user value is implicit (regression fixes C3 #2 and #3) |
| REVUE-211 | Migrate posting to `poster.py` | Technical | ⚠️ Technical title — user value is implicit (dedup, position resolution) |

**Verdict:** All four titles are technically framed. This is an accepted exception for brownfield refactors where the value is regression repair and SOLID compliance, not a new user-visible feature. The regression context (MR !22 failures) is documented in the architecture doc and is the implicit justification. No violation raised — but the downstream tickets (REVUE-209/213/214) should reference their specific regression fixes in their user story bodies when they are written up.

### B. Epic Independence Validation

| Ticket | Blocker | Direction | Valid? |
|--------|---------|-----------|--------|
| REVUE-208 | None | Independent | ✅ |
| REVUE-209 | REVUE-208 | Backward dependency | ✅ |
| REVUE-210 | REVUE-208 | Backward dependency | ✅ |
| REVUE-211 | REVUE-208 (best after 212+213) | Backward dependency | ✅ |

No forward dependencies. No circular dependencies. Dependency graph is a DAG with REVUE-208 as the root. ✅

### C. Story Quality — REVUE-208 AC Review

| AC | Statement | Testable? | Issue |
|----|-----------|-----------|-------|
| AC1 | Create `models.py` with `AgentFinding`, `SynthesisGroup`, `ConsolidatedFinding` dataclasses | Partially | 🟠 Dataclass field names and types not specified in ticket — developer must read architecture doc |
| AC2 | Add Protocol definitions: `GroupingStrategy`, `SynthesisStrategy`, `FindingPostProcessor` | Partially | 🟡 Method signatures not in ticket — developer must read Decision 4 in architecture doc |
| AC3 | Create empty stub modules: `consolidator.py`, `body_builder.py`, `poster.py` | Yes | 🟡 "Empty" is ambiguous — `pass`? `raise NotImplementedError`? bare importable file? |
| AC4 | No changes to `cli.py` or existing tests | Yes | ✅ Clear and verifiable |
| AC5 | All existing tests pass unmodified | Yes | ✅ Clear and verifiable |

**Test Case gaps:**

| Test Case | Issue |
|-----------|-------|
| "Unit tests cover all dataclass fields and validation" | 🟠 "Validation" is undefined — post-init validation? field type constraints? default values? |
| "Protocol signatures verified with mypy" | 🟡 Mypy config not specified — `--strict` vs default changes what passes |
| "Existing test suite passes with zero regressions" | ✅ Clear |

### D. Parent Epic

✅ **Resolved (2026-05-02):** REVUE-208 (and the full Track 1 initiative) is linked to **REVUE-87** (E8 — Review Intelligence & Knowledge Base). Epic link confirmed in Jira. No gap.

### E. Best Practices Compliance Checklist

| Check | REVUE-208 | Notes |
|-------|-----------|-------|
| Delivers user value | ✅ | Internal developer value stated in user story body |
| Can function independently | ✅ | No blockers |
| Appropriately sized | ✅ | ~200 LOC, 1-sitting review |
| No forward dependencies | ✅ | Explicitly scoped out |
| Database tables created when needed | N/A | No DB changes |
| Clear acceptance criteria | 🟠 | Present but field-level specificity missing |
| FR traceability maintained | ✅ | Architecture doc carries full FR mapping |

### Quality Findings Summary

| Severity | Finding | Count |
|----------|---------|-------|
| 🔴 Critical | None | 0 |
| 🟠 Major | AC field specs absent from ticket (requires arch doc read) | 1 |
| 🟡 Minor | Stub module ambiguity; mypy config unspecified; test case "validation" undefined; downstream ticket user-story bodies not yet written | 4 |
| ✅ Resolved | No parent Epic → confirmed REVUE-87 (E8) | — |

---

## Step 6 — Final Assessment

### Overall Readiness Status

## ✅ READY — With Minor Conditions

REVUE-208 is well-specified and safe to implement. No critical issues block progress. The two major findings are papercuts, not stoppers: the field-level spec gap is fully mitigated by the architecture document (Decision 1 and 4), which is explicitly referenced in the Jira ticket description. The missing parent Epic is a process gap, not a blocker for this PR.

### Critical Issues Requiring Immediate Action

None. No issues block implementation.

### Recommended Next Steps

1. **Before implementing — field spec derivation (done, see Appendix A):** The exact dataclass fields and Protocol signatures have been derived from `agent_loader.py`, `dedup_consolidator.py`, and `cli.py`. Use Appendix A as the implementation spec for `models.py`. This resolves the major finding.

2. **Before implementing — clarify stub definition:** Define "empty stub" as: class declared with `...` body (Ellipsis), not `pass` or `raise NotImplementedError`. This keeps stubs importable and mypy-clean without implying any runtime behaviour.

3. **Post-implementation — mypy gate:** Run `mypy --strict` explicitly as part of the test gate. The current test case ("Protocol signatures verified with mypy") is ambiguous about strictness level — `--strict` is the right bar for new typed contracts.

4. **When writing REVUE-209/213/214:** Add explicit regression-fix context to each user story body (REVUE-209 → "fixes C3 regression #1: attribution visible on grouped comments"; REVUE-210 → "fixes C3 regressions #2 and #3").

5. **Confirmed resolved:** REVUE-87 (E8) is the parent Epic. REVUE-208 Epic link confirmed in Jira.

### Final Note

This assessment identified **5 issues** across **2 categories** (0 critical, 1 major, 4 minor). The major finding (field spec gap) is resolved by Appendix A below. The architecture document (`docs/architecture/comment-posting.md`) is exceptionally detailed and compensates for every gap in the Jira ticket. The planning sequence is sound. Safe to proceed.

---

## Appendix A — Derived Dataclass and Protocol Specifications

*Derived 2026-05-02 from: `src/revue/core/agent_loader.py`, `src/revue/core/dedup_consolidator.py`, `src/revue/cli.py`, `src/revue/core/models.py`.*
*Use this as the implementation spec for `src/revue/comments/models.py`.*

### `Attribution` dataclass

Value object representing one agent's contribution to a finding. Used in `ConsolidatedFinding.attribution`. Frozen to signal it is immutable once created.

```python
@dataclass(frozen=True)
class Attribution:
    agent_name: str    # e.g. "zara", "maya"
    category: str      # e.g. "security", "code-quality"
```

---

### `AgentFinding` dataclass

Raw finding output from a single agent. Populated by `agent_loader.py:_parse_finding_item()`.

```python
@dataclass
class AgentFinding:
    file_path: str                        # source file path
    line_number: int                      # 1-indexed
    severity: str                         # "high" | "medium" | "low" | "info"
    issue: str                            # one-sentence problem statement
    suggestion: str                       # one-sentence recommended fix
    confidence: float                     # 0.0–1.0
    category: str                         # "architecture" | "security" | "performance" | "code-quality"
    agent_name: str                       # e.g. "maya", "zara", "kai", "leo", "nova"
    code_replacement: list[str] | None    # verbatim replacement lines; None if absent
    replacement_line_count: int           # lines being replaced (default 1, capped at 100)
    snippet: str = ""                     # diff context; populated downstream, not by agents
```

**Normalisation already in `agent_loader.py`:**
- Severity: `_SEV_MAP` maps "critical"→"high", "major"→"medium", "minor"→"low", "suggestion"→"info"
- Category: `_AGENT_CANONICAL_CATEGORY` / `_KNOWN_CATEGORIES` normalise to the four canonical values
- `code_replacement`: filtered via `filter_code_replacement()` (excludes non-strings, escapes backticks)
- `replacement_line_count`: capped at `_REPLACEMENT_LINE_COUNT_MAX = 100`

---

### `SynthesisGroup` dataclass

Intermediate grouping produced by `GroupingStrategy.group()`.

```python
@dataclass
class SynthesisGroup:
    findings: list[AgentFinding]          # 1+ items
    file_path: str                        # common file path
    line_range: tuple[int, int]           # (min_line, max_line) across findings
    group_type: str                       # "singleton" | "proximity" | "same_line"
```

**Routing rule:** `group_type == "singleton"` → pass-through (no LLM call). Others → `SynthesisStrategy`.

---

### `ConsolidatedFinding` dataclass

Final typed finding ready for `BodyBuilder`. `attribution` is **required and non-nullable** — this is the structural fix for the MR !22 regressions.

```python
@dataclass
class ConsolidatedFinding:
    file_path: str                            # source file path
    line_number: int                          # primary anchor line
    severity: str                             # "high" | "medium" | "low" | "info"
    issue: str                                # problem statement (may be Nova-synthesised prose)
    suggestion: str                           # fix recommendation (may be Nova-synthesised prose)
    confidence: float                         # 0.0–1.0 (for groups: max of constituents)
    category: str                             # "architecture" | "security" | "performance" | "code-quality"
    attribution: list[Attribution]             # REQUIRED — never empty; see Attribution dataclass
    code_replacement: list[str] | None        # unified replacement lines; None if not applicable
    replacement_line_count: int               # lines being replaced (default 1)
    snippet: str                              # diff context for anchor verification; "" if unavailable
    group_type: str = "singleton"             # "singleton" | "proximity" | "same_line" (for metrics)
```

---

### Protocol definitions

```python
class GroupingStrategy(Protocol):
    """Pass A: cluster raw agent findings into SynthesisGroups."""
    def group(self, findings: list[AgentFinding]) -> list[SynthesisGroup]: ...


class SynthesisStrategy(Protocol):
    """Pass B: synthesise a SynthesisGroup into a ConsolidatedFinding.

    Must populate attribution. On LLM failure, falls back to deterministic
    concatenation with full attribution preserved.
    """
    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding: ...


class FindingPostProcessor(Protocol):
    """Transform or validate a ConsolidatedFinding.

    Return None to drop the finding from the inline stream.
    Return the (possibly modified) finding to keep it.
    """
    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None: ...
```

**Default post-processor chain (order is significant):**
1. `NoOpSuggestionDropper` — runs first; if `code_replacement` stripped of diff sigils equals `snippet`, sets `code_replacement = None`
2. `UnanchoredFindingExtractor` — runs second; if `snippet == ""` AND `code_replacement is None`, removes from inline stream and appends to `summary_sink`

---

### Key source references

| What to check | File | Lines |
|--------------|------|-------|
| `_parse_finding_item()` — field extraction | `src/revue/core/agent_loader.py` | 126–174 |
| Synthesised finding construction | `src/revue/core/dedup_consolidator.py` | 446–457 |
| `synthesis_events` structure | `src/revue/core/dedup_consolidator.py` | 394–399 |
| `_build_merged_comment_body()` — grouping today | `src/revue/cli.py` | 914–935 |
| `_extract_finding_fields()` | `src/revue/cli.py` | 1048–1057 |

---

*Assessment completed: 2026-05-02*
*Assessor: BMad Implementation Readiness Check*
*Ticket: REVUE-208 — Comment posting contracts (models and stub modules)*

---

## Dev Agent Record

### Implementation Notes (2026-05-02)

**Approach:** TDD red-green-refactor. Wrote 33 new tests before any implementation; confirmed RED, then GREEN.

**Protocol signature choice:** Used Appendix A signatures throughout (`GroupingStrategy.group() -> list[SynthesisGroup]`, `SynthesisStrategy.synthesise(group: SynthesisGroup)`) rather than the shorthand in Decision 4 of the architecture doc (`list[list[AgentFinding]]`). Appendix A is internally consistent with the `SynthesisGroup` dataclass; the architecture doc example was a notation shorthand, not the definitive signature.

**Validation added:** `SynthesisGroup.__post_init__` raises `ValueError` on empty `findings`; `ConsolidatedFinding.__post_init__` raises `ValueError` on empty `attribution`. These close the "validation undefined" gap from AC1/test-case review (Step 5, Quality Finding 🟠).

**Stub definition:** All stub classes use `...` (Ellipsis) body — importable, mypy-clean, no runtime behaviour implied. `consolidator.py` locks the full module map from the architecture doc (5 classes: `Consolidator`, `ProximityAndCountGroupingStrategy`, `NovaSingleShotStrategy`, `NoOpSuggestionDropper`, `UnanchoredFindingExtractor`). This is "module map locked, signatures deferred" — REVUE-209/213/214 fill in the implementations.

**No changes to `cli.py`** (AC4 satisfied). Existing tests unmodified (AC5 satisfied — 1133 pre-existing tests all pass).

### File List

| File | Change |
|------|--------|
| `src/revue/comments/models.py` | Modified — appended `Attribution`, `AgentFinding`, `SynthesisGroup`, `ConsolidatedFinding` dataclasses and `GroupingStrategy`, `SynthesisStrategy`, `FindingPostProcessor` Protocols |
| `src/revue/comments/consolidator.py` | New — 5 stub classes |
| `src/revue/comments/body_builder.py` | New — `BodyBuilder` stub class |
| `src/revue/comments/poster.py` | New — `Poster` stub class |
| `src/revue/tests/comments/test_pipeline_models.py` | New — 26 tests for pipeline contract types |
| `src/revue/tests/comments/test_consolidator_stub.py` | New — 5 stub importability tests |
| `src/revue/tests/comments/test_body_builder_stub.py` | New — 1 stub importability test |
| `src/revue/tests/comments/test_poster_stub.py` | New — 1 stub importability test |
| `docs/stories/REVUE-208-implementation-readiness.md` | Modified — status → review, Dev Agent Record added |

### Change Log

- **2026-05-02** — Implemented REVUE-208: pipeline contract dataclasses, Protocol interfaces, and 3 stub modules. 1166 tests pass (1133 pre-existing + 33 new). No regressions.
- **2026-05-02** — Pre-commit code review: 8 patches applied (confidence + severity + line_range + group_type validation; import fixes; docstring fix). 1177 tests pass. Pre-commit code review passed. High findings: 0.

### Review Findings (2026-05-02)

- [x] [Review][Patch] `confidence` field has no range validation — added `__post_init__` 0.0–1.0 check to `AgentFinding` and `ConsolidatedFinding` [src/revue/comments/models.py]
- [x] [Review][Patch] `severity` field is unvalidated `str` — added `Literal["high","medium","low","info"]` annotation + `__post_init__` guard [src/revue/comments/models.py]
- [x] [Review][Patch] `SynthesisGroup.line_range` ordering unchecked — added `line_range[0] ≤ line_range[1]` assertion [src/revue/comments/models.py]
- [x] [Review][Patch] `group_type` is unconstrained `str` — added `Literal["singleton","proximity","same_line"]` annotation + `__post_init__` guard [src/revue/comments/models.py]
- [x] [Review][Patch] Import ordering violation — fixed: `enum` now precedes `typing` [src/revue/comments/models.py:4]
- [x] [Review][Patch] `field` imported but never used — removed, import is now `from dataclasses import dataclass` [src/revue/comments/models.py:4]
- [x] [Review][Patch] `consolidator.py` docstring swaps ticket descriptions — simplified to "Full implementation delivered in REVUE-210." [src/revue/comments/consolidator.py:3]
- [x] [Review][Patch] `body_builder.py` and `poster.py` missing `from __future__ import annotations` — already present (false finding, dismissed)
- [x] [Review][Defer] Singleton `AIReview → ConsolidatedFinding` migration may crash — `synthesised_from=None` singleton path needs `Attribution` construction in REVUE-209 [src/revue/core/dedup_consolidator.py] — deferred, REVUE-209 scope
- [x] [Review][Defer] Stub classes do not inherit Protocol interfaces — mypy won't catch signature typos until call site [src/revue/comments/consolidator.py] — deferred, downstream tickets

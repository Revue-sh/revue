---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments:
  - docs/planning/prd.md
  - docs/architecture/comment-posting.md
  - docs/planning/comment-posting-refactor-plan.md
  - _bmad-output/planning-artifacts/ux-design-specification.md
  - docs/stories/REVUE-208-implementation-readiness.md
ticket: REVUE-209
status: done
---

# Implementation Readiness Assessment Report

**Date:** 2026-05-03
**Project:** revue.io
**Ticket:** REVUE-209 — Migrate body building to comments/body_builder.py

---

## Step 1 — Document Discovery

### Document Inventory

| Document | Type | Path | Status |
|----------|------|------|--------|
| REVUE-209 Jira ticket | Ticket | Jira (urukia.atlassian.net) | ✅ Found |
| Comment Posting Architecture | Architecture | `docs/architecture/comment-posting.md` | ✅ Found |
| Track 1 Delivery Plan | Planning | `docs/planning/comment-posting-refactor-plan.md` | ✅ Found |
| Product Requirements Document | PRD | `docs/planning/prd.md` | ✅ Found |
| UX Design Specification | UX | `_bmad-output/planning-artifacts/ux-design-specification.md` | ✅ Found |
| REVUE-208 Readiness Report | Reference | `docs/stories/REVUE-208-implementation-readiness.md` | ✅ Found (Appendix A contains derived field specs) |

### No Duplicates Found

All documents are singular and non-conflicting.

### Context Note

REVUE-209 is PR 2 of the 5-PR Track 1 migration. It fills in the `BodyBuilder` implementation stub created in REVUE-208. REVUE-212/213/214 referred to in earlier planning docs were never created in Jira; REVUE-209 is the actual ticket for this work.

---

## Step 2 — PRD Analysis

### Functional Requirements

*Extracted from `docs/planning/prd.md` — scoped to comment-posting and body-building layer.*

| ID | PRD Section | Requirement |
|----|-------------|-------------|
| FR1 | §3.2 | Inline review comments with severity levels (P0 MVP) |
| FR2 | §3.2 | Sage (Resolver) — scoped, confidence-gated fix suggestions posted as platform-native suggestions (1-click accept) |
| FR3 | §4.3 | Nova merges, deduplicates, and prioritises findings from multiple agents before posting |
| FR4 | §4.3 | Agent attribution preserved through the full consolidation pipeline into posted comments |
| FR5 | §4.4 | Sage posts fix as GitHub "Suggested Change" or GitLab "Apply Suggestion" — platform-specific syntax |
| FR6 | §5.1 | Per-platform comment position model (GitHub: diff-position offset; GitLab: line_code hash) |
| FR7 | §8.1 | Summary comment posted to the PR/MR (configurable: `summary_comment: true`) |
| FR8 | §8.1 | Inline comments posted on specific lines (configurable: `inline_comments: true`) |
| FR9 | §10.1 | Inline comment format: severity badge + issue description + remediation block + agent attribution + Sage suggestion (where applicable) |
| FR10 | §10.1 | Platform-native suggestion block rendered as fenced code (developer accepts 1-click) |
| FR11 | §10.2 | PR-level summary comment: findings grouped by severity + strengths section + Sage suggestions section |
| FR12 | §13 | Graceful degradation: if an agent fails, Nova proceeds with available findings; failing agent contribution marked as unavailable in summary |
| FR13 | §13 | Diff hard limit: post a single comment explaining the limit when exceeded — non-blocking warning |
| FR14 | §7.1 | All agents (Zara, Kai, Maya, Leo) produce findings with: severity, file/line, description, remediation |

**Total FRs extracted (comment-posting scope): 14**

### Non-Functional Requirements

| ID | PRD Section | Requirement |
|----|-------------|-------------|
| NFR1 | §3.4 / §13 | Review completes within 3 minutes for diffs up to 2,000 changed lines |
| NFR2 | §4.2 / §13 | Source code and diffs never stored by Revue's cloud backend |
| NFR3 | §3.4 | False positive rate < 15% at MVP |
| NFR4 | §13 | Monorepo support — path-scoped configuration |
| NFR5 | §4.2 | Webhook signature verification before processing |

**Total NFRs extracted: 5**

### Additional Requirements / Constraints

- `ConsolidatedFinding` is the typed handshake between Consolidator and BodyBuilder — raw dicts across this boundary prohibited (architecture Decision 1)
- `attribution` is a **required** non-nullable field in `ConsolidatedFinding` (architecture Decision 1) — makes the MR !22 attribution regressions structurally impossible
- Platform suggestion fences must use a registry pattern — no if/elif chains (project coding standard: OCP)
- `BodyBuilder` is a pure function module — no AI calls, no VCS I/O (architecture Decision 2 / REVUE-209 ticket Notes)
- `.revue.yml` `consolidation:` stanza to be documented (architecture Decision 2) — out of scope for REVUE-209 (REVUE-213 scope)

### PRD Completeness Assessment

The PRD is well-specified for comment-posting requirements. Sections §10.1 and §10.2 provide exact output format examples (severity badge syntax, suggestion block format, summary structure). The platform-specific divergence (GitHub vs GitLab suggestion fences) is addressed in §4.4 and §5.1. No PRD gaps identified that block REVUE-209.

---

## Step 3 — Epic Coverage Validation

*Epic source: REVUE-87 (E8) in Jira — Track 1 ticket structure: REVUE-208 (Done), REVUE-209 (this story), consolidator ticket (next), poster ticket (last). FR coverage derived from REVUE-208 readiness report + REVUE-209 ACs + comment-posting-refactor-plan.md.*

### FR Coverage Matrix

| FR | PRD Requirement (summary) | Ticket / Scope | Status |
|----|--------------------------|----------------|--------|
| FR1 | Inline comments with severity levels | REVUE-209 AC1/AC2 — `build()` renders severity badge | ✅ Covered |
| FR2 | 1-click fix suggestions (platform-native) | REVUE-209 AC1/AC3 — singleton with `code_replacement` + platform fences | ✅ Covered |
| FR3 | Nova merge/dedup/prioritise findings | REVUE-213 (consolidator ticket — separate story) | ✅ Covered (out of REVUE-209 scope) |
| FR4 | Agent attribution preserved into posted comments | REVUE-209 AC2 — grouped renderer includes attribution header per item | ✅ Covered |
| FR5 | Platform-native suggestion fence (GitHub vs GitLab vs Bitbucket) | REVUE-209 AC3 — registry-based platform dispatch | ✅ Covered |
| FR6 | Per-platform position model (diff offset vs line_code hash) | REVUE-214 (poster ticket — separate story) | ✅ Covered (out of REVUE-209 scope) |
| FR7 | Summary comment posted to PR/MR | REVUE-209 AC2 — `build_summary()` method | ✅ Covered |
| FR8 | Inline comments on specific lines | REVUE-209 AC1 — `build()` produces inline comment body | ✅ Covered |
| FR9 | Inline comment format: severity badge + issue + remediation + attribution | REVUE-209 AC1/AC2 — all four comment shapes | ✅ Covered |
| FR10 | Platform-native suggestion block (fenced code, 1-click) | REVUE-209 AC3 — per-platform fences | ✅ Covered |
| FR11 | PR-level summary: severity groups + strengths + Sage suggestions | REVUE-209 AC2 — `build_summary()` + `summary_sink` | ✅ Covered |
| FR12 | Graceful degradation — failing agent marked unavailable in summary | REVUE-209 AC2 — `UnanchoredFindingExtractor` → `summary_sink` handles unanchored findings | ✅ Covered (partial — unavailable agent label is Nova/consolidator concern; BodyBuilder renders the demoted section) |
| FR13 | Diff hard limit comment | Already implemented elsewhere; out of REVUE-209 scope | ✅ Covered (out of scope) |
| FR14 | Finding fields: severity, file/line, description, remediation | REVUE-208 (Done) — `AgentFinding` / `ConsolidatedFinding` dataclasses | ✅ Covered (Done) |

### Missing Requirements

**None.** All 14 comment-posting FRs have a traceable implementation path. FR12's "agent unavailable" label is a Nova/Consolidator concern resolved in the consolidator ticket; BodyBuilder's role is to correctly render the `summary_sink` section, which is covered by AC2.

### Coverage Statistics

- Total PRD FRs (comment-posting scope): 14
- FRs covered by Track 1 ticket structure: 14
- FRs directly implemented by REVUE-209: 9 (FR1, FR2, FR4, FR5, FR7, FR8, FR9, FR10, FR11)
- FRs covered by adjacent tickets: 5 (FR3→consolidator, FR6→poster, FR12 partial→consolidator, FR13→existing, FR14→REVUE-208)
- **Coverage: 100%**

---

## Step 4 — UX Alignment Assessment

### UX Document Status

**Found:** `_bmad-output/planning-artifacts/ux-design-specification.md` (completed 2026-04-25).

### UX ↔ PRD Alignment

| UX Decision | PRD Requirement | Alignment |
|-------------|----------------|-----------|
| D1 — Severity-first visual hierarchy (`🔴/🟡/🔵 [SEVERITY]`) | FR1: inline comments with severity levels; FR9: severity badge first in comment format | ✅ Aligned |
| D2 — Agent attribution per finding (`*Zara · Security*`) | FR4: attribution preserved through pipeline; FR9: comment format includes attribution | ✅ Aligned |
| D3 — Brand footer on all inline comments (`— 🤖 Revue`) | FR9: comment format spec | ✅ Aligned |
| D4 — Nova as synthesiser (unified fix for multi-agent findings) | FR3: Nova merge/dedup; FR11: summary comment | ✅ Aligned — synthesis content is produced by Consolidator; BodyBuilder renders it |
| D5 — Configurable comment vocabulary (`action/suggest/note`) | FR9: comment format | ⚠️ Partially aligned — REVUE-209 ticket does not mention vocabulary label rendering; BodyBuilder must default to "Action/Suggest/Note" |
| D6 — Platform-native suggestion blocks (opt-out) | FR2: 1-click fix; FR10: suggestion block | ✅ Aligned |
| D7 — Unified comment structure (single, multi, summary) | FR9: inline format; FR11: summary format | ✅ Aligned |

### UX ↔ Architecture Alignment

| UX Requirement | Architecture Support | Alignment |
|----------------|---------------------|-----------|
| Severity badge first in every comment | `ConsolidatedFinding.severity` required field; BodyBuilder renders severity as primary anchor | ✅ Supported |
| Attribution per item in multi-finding comments | `ConsolidatedFinding.attribution: list[Attribution]` required non-nullable (Decision 1) | ✅ Supported |
| Platform detection is a single branch point | REVUE-209 AC3: platform registry (no if/elif) | ✅ Supported |
| All platform adapters receive fully assembled `body: str` | BodyBuilder is a pure function returning `str`; no adapter-level branching | ✅ Supported |
| `<details>/<summary>` progressive disclosure on GitHub/GitLab | UX D7 mentions progressive disclosure (Tier 1 only) | ⚠️ Not in REVUE-209 ACs — BodyBuilder may need to wrap explanatory content in `<details>` for Tier 1. Not explicitly required in ticket. Flag for clarification. |
| Brand footer + fingerprint sentinel on every inline comment | UX D3 + D7 template: `— 🤖 Revue` + `[//]: # (revue:fp:{fingerprint})` | ⚠️ Not explicitly called out in REVUE-209 ACs — must be present in BodyBuilder output per UX spec but is not listed as a testable AC |

### Alignment Issues

None critical. Two warnings raised:

1. **⚠️ Minor — Vocabulary labels not in ACs:** UX D5 requires the `action/suggest/note` label system. REVUE-209 does not list this as an AC or test case. BodyBuilder should hardcode defaults (`Action`, `Suggest`, `Note`) for MVP with a config hook deferred to a separate ticket. Risk: if omitted entirely, the comment format diverges from the UX spec.

2. **⚠️ Minor — Brand footer + fingerprint not in ACs:** UX D3 and D7 require `— 🤖 Revue` and `[//]: # (revue:fp:{fingerprint})` at the end of every inline comment. These are present in `cli.py` today but the REVUE-209 ACs do not explicitly call out migrating them into BodyBuilder. Risk: if forgotten, post-migration comments lose the deduplication fingerprint sentinel — causing re-posts on re-run.

---

## Step 5 — Epic Quality Review

### A. User Value Focus Check

| Aspect | Assessment |
|--------|-----------|
| Epic title | Technical framing ("Migrate body building") — accepted exception for brownfield refactors |
| User value statement | Present in Jira ticket user story body: "comment rendering is isolated, testable, and free from the MR !22 attribution regressions" |
| Tangible user benefit | ✅ C3 regression #1 fixed (attribution visible on grouped comments) — developer-visible improvement |

**Verdict:** 🟡 Technical title is an accepted exception for SOLID/brownfield work (per project feedback memory). User value is implicit but clearly stated in the background section. No violation raised.

### B. Epic Independence Validation

| Dependency | Direction | Valid? |
|-----------|-----------|--------|
| REVUE-208 (typed contracts) | Backward — REVUE-208 is Done ✅ | ✅ |
| Consolidator ticket (next) | No forward dependency | ✅ |
| Poster ticket (last) | No forward dependency | ✅ |

No forward dependencies. REVUE-209 can be completed and merged independently. ✅

### C. Story Quality — REVUE-209 AC Review

| AC | Statement | Testable? | Issue |
|----|-----------|-----------|-------|
| AC1 | `build(finding: ConsolidatedFinding) -> str` + `build_summary(...)` method signatures | Partially | 🔴 **CRITICAL** — Missing `fp: str` parameter. Fingerprint (`[//]: # (revue:fp:{fp})`) is generated upstream in the posting loop and *must* be embedded in the body string by BodyBuilder. Current `cli.py` passes `fp` to both `_build_merged_comment_body(fp)` and the inline body builder (line 1127). A `build()` that returns body without `fp` breaks deduplication on re-run. |
| AC2 | Four comment shapes; "attribution header per item" | Partially | 🟠 **Major** — Attribution format unspecified (`*Zara · Security*` per UX D2/D7 and current cli.py lines 1112–1118). Also: brand footer `— 🤖 Revue` not mentioned; current cli.py embeds it. Missing it breaks UX D3. |
| AC3 | Platform registry (no if/elif) | Yes | ✅ Clear and testable |
| AC4 | `cli.py` updated to call `BodyBuilder` | Partially | 🟡 **Minor** — "updated to call" is vague; doesn't specify the `platform` argument type (str? Enum?), how `fp` flows to `build()`, or what happens to `_extract_finding_fields` (referenced in AC4 text but not in AC5 deletion list) |
| AC5 | `_build_merged_comment_body` deleted | Yes | ✅ Clear — but `_extract_finding_fields` (line 659 of cli.py) is also dead after REVUE-209; not listed for deletion |
| AC6 | All existing tests pass | Yes | ✅ |
| AC7 | New unit test list | Partially | 🟠 **Major** — Missing: `test_fingerprint_sentinel_present` (sentinel must survive migration), `test_brand_footer_present` (UX D3), `test_vocabulary_label_default` (action/suggest/note) |
| AC8 | C3 regression #1 not reproducible | Yes | ✅ |

### D. `details` Field Mapping Gap

🟠 **Major finding (implementation trap):** Current `cli.py` single-finding path has four text fields: `issue` (one-sentence header), `details` (body paragraph), `rec` (recommendation), `code_replacement`. `ConsolidatedFinding` has: `issue` (one-sentence problem statement), `suggestion` (one-sentence fix), `code_replacement`. There is no `details` field in `ConsolidatedFinding`.

The REVUE-209 ticket does not address how the old `details` paragraph maps into the new model. Two valid interpretations:
- Option A: `issue` in `ConsolidatedFinding` is the full multi-sentence description (absorbs old `details`) — BodyBuilder renders it as the body paragraph directly.
- Option B: `details` is dropped in the new model; Nova synthesis produces a richer `issue` field that makes the old `details` paragraph redundant.

The architecture doc does not disambiguate. The implementation will need to make a judgment call; if unaddressed in the ticket, different developers may make different choices. **The AC should specify which interpretation is intended.**

### E. Best Practices Compliance Checklist

| Check | Status | Notes |
|-------|--------|-------|
| Delivers user value | ✅ | Attribution regression fix is user-visible |
| Functions independently | ✅ | REVUE-208 (only dep) is Done |
| Appropriately sized | ✅ | ~1–2 days, one sitting review |
| No forward dependencies | ✅ | Confirmed |
| Database tables when needed | N/A | No DB changes |
| Clear acceptance criteria | 🔴 | `fp` parameter gap in AC1 is critical |
| FR traceability | ✅ | 9 FRs directly covered |

### Quality Findings Summary

| Severity | Finding | Count |
|----------|---------|-------|
| 🔴 Critical | `build()` method signature missing `fp: str` — deduplication sentinel will be dropped on migration | 1 |
| 🟠 Major | Brand footer + fingerprint not in ACs; attribution format unspecified; test suite missing 3 test cases; `details` field mapping ambiguous | 4 |
| 🟡 Minor | `_extract_finding_fields` not listed for deletion; `platform` argument type unspecified; vocabulary labels absent | 3 |

---

## Step 6 — Final Assessment

### Overall Readiness Status

## ⚠️ NEEDS WORK — 1 Critical Fix Before Implementation Starts

REVUE-209 is well-scoped and the architecture is sound. However, one critical gap in AC1 will cause a production regression if implemented as written. The remaining major findings are clarifications that eliminate implementation ambiguity — they should be resolved before the first commit but do not require blocking the branch.

---

### Critical Issues Requiring Immediate Action

**🔴 [Critical] AC1 method signature missing `fp: str` — deduplication will silently break**

The current `build(finding: ConsolidatedFinding) -> str` signature does not include the fingerprint. However, `cli.py` embeds `[//]: # (revue:fp:{fp})` in every inline comment body (lines 1127 and 934). The fingerprint is generated upstream in the posting loop and must be passed to `BodyBuilder.build()`. Without it:
- Re-runs will re-post every comment (dedup reads the sentinel from existing comments)
- Every existing test that scans for the sentinel will lose coverage

**Fix:** Change AC1 to: `build(finding: ConsolidatedFinding, fp: str) -> str`

---

### Recommended Next Steps

1. **Before first commit — fix AC1:** Update the method signature to `build(finding: ConsolidatedFinding, fp: str) -> str` and add `test_fingerprint_sentinel_present` to the test case list.

2. **Before first commit — resolve `details` field mapping:** Confirm interpretation: `ConsolidatedFinding.issue` absorbs what was `_extract_finding_fields.details` (Option A). Add a clarifying sentence to the ticket Notes section.

3. **Before first commit — add brand footer to AC2:** Explicitly add `— 🤖 Revue` (UX D3) and fingerprint sentinel to the four comment shape descriptions in AC2. Add `test_brand_footer_present` to the test case list.

4. **Before first commit — specify attribution format:** AC2 should state the format: `*{agent_display_name} · {category_title_case}*` per cli.py lines 1115–1118 and UX D2.

5. **During implementation — default vocabulary labels:** Hardcode `💡 **Recommendation:**` → `> 💡 **Action:**` / `> 💡 **Suggestion:**` / `> ℹ️ Note:` as defaults (UX D5), with a `# TODO: wire to .revue.yml vocabulary stanza` comment. Do not implement the configurable stanza in this story.

6. **At cleanup — delete `_extract_finding_fields`:** Line 659 of `cli.py` becomes dead code after this PR. Add it to AC5.

7. **When writing the consolidator ticket (REVUE-213):** The `details` field clarification (Step 5D) informs how `NovaSingleShotStrategy` builds `ConsolidatedFinding.issue` from agent output — confirm the contract is consistent.

---

### Final Note

This assessment identified **8 issues** across **3 categories** (1 critical, 4 major, 3 minor). The critical finding is a method signature gap that would cause a silent dedup regression — it is a one-line ticket fix, not a redesign. The four major findings are clarification gaps that reduce ambiguity for the implementer. The architecture document (`docs/architecture/comment-posting.md`) and the existing `cli.py` implementation together are sufficient to fill every gap identified here without reopening design discussions.

---

*Assessment completed: 2026-05-03*
*Assessor: BMad Implementation Readiness Check*
*Ticket: REVUE-209 — Migrate body building to comments/body_builder.py*

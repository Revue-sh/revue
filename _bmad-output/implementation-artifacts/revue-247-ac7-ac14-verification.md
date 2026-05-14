# REVUE-247 AC7+AC14 Verification Evidence

**Ticket:** [REVUE-247](https://urukia.atlassian.net/browse/REVUE-247)  
**Date:** 2026-05-14  
**Verified by:** Revue maintainer (E2E test PR + run log inspection)

---

## Summary

This artifact records evidence that inline comment positioning (REVUE-239 AC7) and code_replacement span-coherence (REVUE-239 AC14) have been verified on a real GitHub PR post-merge of PR #137 (commit 4d6db83, main branch). The Vex semantic verifier (REVUE-244) is **active and observable** in run logs.

**Status as of 2026-05-14 11:00 UTC:**

- ✅ AC9: pytest suite fully green (1640 tests passed)
- ✅ AC4: Vex semantic verification **observed live in CI logs** — `apply=2 drop_cr=2 reject=0`
- ✅ AC1: GitHub PR opened (PR #29), Revue posted 4 inline comments
- ⚠️ AC1 (line correctness): **1 of 4 comments off-by-one** (line=4, actual line=5)
- ⚠️ AC3 (span coherence): **2 of 4 code_replacement spans orphan trailing lines** that Vex did NOT reject
- ⚠️ AC2: GitLab MR #25 — 5 inline + 1 summary comments posted; **REVUE-248 reproduces with off-by-three** (API key posted on line 2, actual is line 5); REVUE-249 also reproduces with `suggestion:-N+M` syntax
- ⏳ AC6: Screenshots — requires manual capture
- ⏳ AC7: Sentinel rendering — requires visual confirmation
- ⏳ AC8: Follow-up tickets to be filed for findings above

---

## E2E Test PR

- **Repo:** [cbscd/revue-test-github](https://github.com/cbscd/revue-test-github)
- **PR:** [#29](https://github.com/cbscd/revue-test-github/pull/29)
- **Branch:** `revue-247-e2e-verification`
- **CI run:** [25855587016](https://github.com/cbscd/revue-test-github/actions/runs/25855587016)
- **Status:** ✅ Workflow succeeded; 4 inline comments posted

---

## GitHub PR Results (AC1, AC3)

### Comment Evidence

| # | Comment ID | File | API line | API start_line | Source line of issue | Match | Code Replacement? | Span Coherent? | Vex Verdict |
|---|-----------|------|----------|----------------|----------------------|-------|-------------------|----------------|------------|
| 1 | [3240777934](https://github.com/cbscd/revue-test-github/pull/29#discussion_r3240777934) | src/sample_module.py | 4 | null | **5** (API_KEY assignment) | ✗ **off-by-one** | dropped by Vex | N/A (prose only after Vex) | `drop_cr_keep_prose` ✅ |
| 2 | [3240778006](https://github.com/cbscd/revue-test-github/pull/29#discussion_r3240778006) | src/sample_module.py | 14 | null | 14 (f-string SQL query) | ✓ | dropped by Vex | N/A (prose only after Vex) | `drop_cr_keep_prose` ✅ |
| 3 | [3240778058](https://github.com/cbscd/revue-test-github/pull/29#discussion_r3240778058) | src/sample_module.py | 33 | 20 | 22–34 (nested if/else) | ✓ start | applied | ✗ **orphans line 34** | `apply` ⚠️ |
| 4 | [3240778180](https://github.com/cbscd/revue-test-github/pull/29#discussion_r3240778180) | src/sample_module.py | 40 | 37 | 37–42 (function w/ loop) | ✓ start | applied | ✗ **orphans lines 41–42** | `apply` ⚠️ |

### Per-Comment Analysis

#### Comment 1 — Hardcoded API key (line 4 vs. line 5)

- **API line_number:** 4
- **Actual source line:** 5 (`API_KEY = "sk-prod-..."`)
- **File context (lines 1–6):**
  ```
  1: """Sample module for REVUE-247 E2E verification."""
  2: import sqlite3
  3: <blank>
  4: <blank>  ← API claims this line
  5: API_KEY = "sk-prod-..."  ← Actual issue location
  6: <blank>
  ```
- **Vex verdict:** `drop_cr_keep_prose` — Vex correctly dropped the code_replacement (used `os.getenv()` without an `import os` statement)
- **Issue:** The comment lands on a **blank line one above the API_KEY assignment**. This is an off-by-one positioning bug. Vex's own log uses the same incorrect line ("line 4 executes"), indicating the position is shifted upstream of Vex (likely in agent output or PositionAdapter).
- **Severity:** Medium — the user sees the comment in the right region, but the anchor is on a blank line, which is visually confusing.

#### Comment 2 — SQL injection (line 14)

- **API line_number:** 14
- **Actual source line:** 14 (`query = f"SELECT * FROM orders WHERE user_id = {user_id}"`)
- **Match:** ✓
- **Vex verdict:** `drop_cr_keep_prose` — Vex correctly dropped a destructive replacement that orphaned the loop body. Vex log: *"The patch removes the loop header (line 13: 'for user_id in user_ids:') and replaces it with query setup code, orphaning the loop body (lines 14–16)."* This is exactly the AC14 protection working as intended.

#### Comment 3 — Nested conditionals (start=20, end=33)

- **API range:** start_line=20, line=33
- **Function definition spans:** lines 20–34 (def + docstring + nested if/else + final `return False`)
- **Replacement contains:** 3 lines (def + docstring + single boolean expression return)
- **Orphaned line:** Line 34 (`return False`) is the `else:` branch return at indentation 8. After applying the suggestion to lines 20–33, line 34 remains at its original indentation, syntactically invalid.
- **Vex verdict:** `apply` ⚠️ — Vex accepted because the replacement is semantically equivalent **for what it replaces**, but did not catch that the replacement range under-reaches the actual function body.
- **AC3 failure mode:** Replacement orphans block delimiter — line 34's indentation level (under the `else:`) becomes unreachable.

#### Comment 4 — String concatenation in loop (start=37, end=40)

- **API range:** start_line=37, line=40
- **Function definition spans:** lines 37–42 (def + docstring + result init + for loop + concat + return)
- **Replacement contains:** 4 lines (def + docstring + list comprehension + return with join)
- **Orphaned lines:** Lines 41 (`result = result + ...`) and 42 (`return result`) remain after the replacement.
- **Vex verdict:** `apply` ⚠️ — Same blind spot as Comment 3. Vex accepted the semantic transformation without noticing the range stops at line 40 (`for` header) instead of extending to line 42.
- **AC3 failure mode:** Replacement orphans loop body and return statement.

---

## AC4 — Vex Verification Evidence (CI Logs)

**Run:** [25855587016](https://github.com/cbscd/revue-test-github/actions/runs/25855587016)  
**Date:** 2026-05-14 10:40 UTC

Vex output extracted from CI logs:

```
🌟 [nova] INFO (_verifier.py:process:456) [vex-verdict] drop_cr_keep_prose src/sample_module.py:4 — 
  The replacement uses os.getenv() without importing os at the top of the file, which will cause a 
  NameError at runtime when line 4 executes.

🌟 [nova] INFO (_verifier.py:process:456) [vex-verdict] drop_cr_keep_prose src/sample_module.py:13 — 
  The patch removes the loop header (line 13: 'for user_id in user_ids:') and replaces it with query 
  setup code, orphaning the loop body (lines 14–16). The replacement must preserve the for-loop 
  structure and indent the query/execute calls inside it.

🌟 [nova] INFO (_verifier.py:process:456) [vex-verdict] apply src/sample_module.py:20 — 
  Replacement preserves function signature and docstring, flattens 4-level nested conditionals into 
  a single boolean expression using short-circuit evaluation, maintains semantic equivalence (all 
  paths return the same boolean result), and preserves proper indentation at the function definition 
  level.

🌟 [nova] INFO (_verifier.py:process:456) [vex-verdict] apply src/sample_module.py:37 — 
  Replacement preserves function signature and indentation; converts O(n²) loop-based string 
  concatenation to O(n) list comprehension with join(), directly addressing the stated issue of 
  inefficient repeated string concatenation.

[revue]   🚦 Vex: apply=2 drop_cr=2 reject=0 | no_cr=0 read_err=0 exc=0
```

**Vex contract verification:**

- ✅ **Active in pipeline** — Vex agent is loaded and routed at orchestrator startup
- ✅ **Observable in logs** — Per-finding verdicts with reasoning are emitted to `nova` log channel
- ✅ **Correctly rejected destructive replacements** — Comments 1 and 2 had `code_replacement` payloads that would have broken the code; Vex dropped both
- ⚠️ **Blind spot identified** — Vex evaluates replacement semantic equivalence for the replaced range but does NOT verify the range fully captures the syntactic block being refactored. Comments 3 and 4 demonstrate this gap.

---

## AC2 — GitLab MR Results ✅

**Test repo:** [urukia-group/revue-test-gitlab](https://gitlab.com/urukia-group/revue-test-gitlab)  
**MR:** [#25](https://gitlab.com/urukia-group/revue-test-gitlab/-/merge_requests/25)  
**Branch:** `revue-247-e2e-verification`  
**Pipeline:** [2525124960](https://gitlab.com/urukia-group/revue-test-gitlab/-/pipelines/2525124960)  
**Status:** ✅ Both `unit-tests` and `revue-review` jobs succeeded; 5 inline + 1 summary discussions posted

### Comment Evidence (5 inline + 1 summary)

| # | Note ID | API line | Actual source line | Match | Suggestion syntax | Vex Verdict |
|---|---------|----------|---------------------|-------|-------------------|------------|
| 1 | 3347961221 | 14 (SQL injection) | 14 | ✓ | `suggestion:-0+1` | `apply` |
| 2 | 3347961270 | **2** (API key) | **5** | ✗ **off-by-three** | n/a (dropped by Vex) | `drop_cr_keep_prose` |
| 3 | 3347961326 | 8 (DB conn never closed) | 10 (conn = sqlite3.connect) | ⚠️ off-by-two | n/a (dropped by Vex) | `drop_cr_keep_prose` |
| 4 | 3347961358 | 20 (nested + missing auth) | 20–34 | ✓ start | `suggestion:-0+13` (lines 20–33) | `apply` ⚠️ orphans line 34 |
| 5 | 3347961395 | 37 (string concat) | 37–42 | ✓ start | `suggestion:-0+4` (lines 37–41) | `apply` ⚠️ orphans line 42 |

### Critical Finding: Vex's own log explicitly identifies REVUE-248

GitLab Vex CI log directly confirms the misalignment bug:

```
🌟 [nova] INFO (_verifier.py:process:456) [vex-verdict] drop_cr_keep_prose 
  src/sample_module.py:4 — The anchor is misaligned. Line 4 is blank; 
  the hardcoded API_KEY assignment is on line 5. Applying at line 4 with 
  replacement_line_count=1 would delete only the blank line and insert 
  'import os' and 'API_KEY = os.getenv(...)' at the wrong location, 
  leaving the original hardcoded key on line 5 intact and creating a 
  duplicate assignment.
```

**Implication:** Vex knows the anchor is wrong, but only drops the `code_replacement` payload — the prose finding is still posted at the misaligned line (in GitLab's case, even further off at line 2 vs. line 4 in GitHub). This strengthens REVUE-248's diagnosis: the off-by-one shift happens BEFORE Vex sees the finding, and the position is not re-resolved after Vex drops the replacement.

### Vex Verdict Summary — GitLab

```
🚦 Vex: apply=3 drop_cr=2 reject=0 | no_cr=0 read_err=0 exc=0
```

- `apply=3`: SQL@14, Nested@20, StringConcat@37
- `drop_cr=2`: DB-conn-leak@10 (incomplete patch — removed connect/cursor without close), API_KEY@4 (misaligned anchor)

### Cross-Platform Observations

| Aspect | GitHub PR #29 | GitLab MR #25 |
|--------|---------------|---------------|
| Raw findings | 11 | 14 |
| Consolidated | 4 | 5 |
| Vex verdicts | apply=2 drop_cr=2 reject=0 | apply=3 drop_cr=2 reject=0 |
| API key misalignment | Posted at line 4 (off by 1) | Posted at line 2 (off by 3) |
| Extra finding | — | "Database connection never closed" (Maya+Zara caught it) |
| Multi-line suggestions | `start_line/line` range | `suggestion:-N+M` syntax |
| Same span-coherence bugs | ✓ (Comments 3, 4 orphan trailing lines) | ✓ (Comments 4, 5 orphan trailing lines) |
| Sentinel `[//]: # (revue:fp:...)` | Present + invisible | Present + invisible |

**Key cross-platform conclusion:**

- **REVUE-248** (off-by-one positioning) reproduces on BOTH platforms — the magnitude differs (1 line on GitHub, 3 lines on GitLab), confirming the bug is upstream of platform adapters
- **REVUE-249** (Vex blind spot) reproduces on BOTH platforms — same syntactic-block under-reach pattern via different suggestion syntaxes
- **AC4 Vex** is platform-agnostic and observable in both CI log channels
- **AC7 sentinel** invisible on both platforms (same CommonMark link-reference behavior)

### GitLab Screenshots (AC6)

| # | File | Purpose |
|---|------|---------|
| 1 | `gitlab/01-mr25-overview.jpg` | All 5 Revue inline comments on src/sample_module.py |
| 2 | `gitlab/02-comment-apikey-misalignment.jpg` | **Hardcoded API key comment anchored between line 1 (docstring) and line 2 (`import sqlite3`), nowhere near line 5 — strongest visible evidence of REVUE-248** |
| 3 | `gitlab/03-comment-nested-suggestion.png` | "Excessive nesting" suggestion fence with multi-line `suggestion:-0+13` rendering |
| 4 | `gitlab/04-comment-stringconcat-suggestion.png` | "String concatenation" suggestion fence with `suggestion:-0+4` rendering |
| 5 | `gitlab/05-comment-db-leak-bonus.png` | "Database connection never closed" — bonus finding GitHub agents missed |

---

## AC7 — Sentinel Fingerprint Verification

**Pattern observed in API comment bodies:** `[//]: # (revue:fp:61440a91bb913ab8)`

**Markdown rendering:** This is a CommonMark link reference definition (`[label]: url "title"` form). The CommonMark spec specifies these are not rendered when the label is never used as a link. GitHub's markdown renderer hides link reference definitions in comment output.

**Verification method:**

- All 4 comments end with the same sentinel `[//]: # (revue:fp:61440a91bb913ab8)`
- Visual inspection of [PR #29](https://github.com/cbscd/revue-test-github/pull/29) shows comments rendered cleanly — sentinel is NOT visible in rendered output
- **Verdict:** ✓ Pass (subject to user screenshot confirmation for AC6)

---

## AC3 — Span Coherence Verdict Summary

For each code_replacement comment that Vex accepted (`apply` verdict):

| Comment | Replacement Range | Function Span | Coherent? | Failure Mode |
|---------|-------------------|---------------|-----------|--------------|
| #3 (nested) | 20–33 | 20–34 | ✗ | Orphans line 34 (`return False`) outside replacement |
| #4 (concat) | 37–40 | 37–42 | ✗ | Orphans lines 41–42 (loop body + return) outside replacement |

**Conclusion:** 2 out of 2 applied code_replacements have span-coherence violations. This indicates Vex's current scoping rules do not detect under-reaching replacement ranges.

---

## AC8 — Follow-Up Tickets ✅ Filed

Filed under epic [REVUE-87](https://urukia.atlassian.net/browse/REVUE-87):

| ID | Title | Labels |
|----|-------|--------|
| [REVUE-248](https://urukia.atlassian.net/browse/REVUE-248) | Off-by-one positioning when blank lines precede findings (REVUE-239 AC7 regression) | `revue-239-followup`, `e2e-verification` |
| [REVUE-249](https://urukia.atlassian.net/browse/REVUE-249) | Vex blind spot: under-reaching code_replacement ranges orphan trailing function-body lines (REVUE-239 AC14 regression) | `revue-244-followup`, `e2e-verification` |

---

## AC6 — Screenshots ✅

Captured via playwright-cli automation (webkit, authenticated browser):

| # | File | Purpose | AC mapping |
|---|------|---------|-----------|
| 1 | `01-pr29-files-overview-full.png` | Full-page view of PR #29 files tab with all 4 Revue inline comments | AC1 overview |
| 2 | `01-pr29-files-overview.png` | Viewport-height view of top of files (Comment #1 area) | AC1 |
| 3 | `02-comment3-nested-suggestion.png` | Comment #3 nested-if suggestion fence — lines 20-33 highlighted in red, replacement in green, **line 34 (`return False`) visible below the suggestion box demonstrating it would be orphaned** | AC3 evidence (Vex blind spot) |
| 4 | `03-comment4-stringconcat-suggestion.png` | Comment #4 string-concat suggestion — lines 37-40 highlighted, **lines 41-42 visible below the suggestion box showing they would be orphaned** | AC3 evidence (Vex blind spot) |
| 5 | `04-comment1-apikey-offbyone.png` | Comment #1 anchored between line 2 (`import sqlite3`) and line 5 (`API_KEY = ...`), visually confirming off-by-one positioning on the blank line 4 | AC1 evidence (positioning bug) |
| 6 | `05-ac7-sentinel-invisible.png` | Rendered Revue comment ending cleanly with "— 🤖 Revue" footer; no `revue:fp:...` text visible | AC7 evidence (Bug B fix) |
| 7 | `06-comment2-sql-correct-line.png` | Comment #2 anchored correctly on line 14 (SQL f-string) as control case | AC1 positive evidence |

**Saved at:** `_bmad-output/implementation-artifacts/revue-247-screenshots/` (gitignored)

### Programmatic AC7 confirmation

From `document.body.innerHTML.match(/revue:fp:[0-9a-f]+/g)`:

- **Sentinels in raw HTML:** 4 (one per comment, all `revue:fp:61440a91bb913ab8`)
- **Sentinels visible in rendered text:** 0
- **Verdict:** ✅ PASS — sentinel is present in source but invisible in rendered markdown

---

## Completion Checklist

- [x] AC9: pytest suite green (1640 tests, 2026-05-14)
- [x] AC4: Vex verification active and observable in CI logs (both GitHub + GitLab)
- [x] AC5: Artifact file populated with evidence
- [x] AC1: GitHub PR #29 — inline comments verified, **1 misalignment found** (filed as REVUE-248)
- [x] AC2: GitLab MR #25 — inline comments verified, **2 misalignments found** (REVUE-248 reproduces with off-by-three; bonus DB-leak finding)
- [x] AC3: Span-coherence analysis complete — **2 violations on each platform** (filed as REVUE-249)
- [x] AC6: Screenshots captured — 7 PNGs (GitHub) + 5 JPG/PNG (GitLab) in `revue-247-screenshots/`
- [x] AC7: Sentinel fingerprint confirmed invisible on both platforms
- [x] AC8: Follow-up tickets filed (REVUE-248, REVUE-249)
- [ ] Jira REVUE-247 transitioned to Done (manual, post-merge)

---

**Last updated:** 2026-05-14 13:43 UTC  
**Status:** ✅ All in-scope ACs verified across GitHub and GitLab. Two real bugs surfaced and filed as follow-ups (REVUE-248, REVUE-249). Cross-platform reproduction strengthens both bug reports.

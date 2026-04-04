# REVUE-94 — Definition of Ready (DoR) Scorecard

**Story:** REVUE-94 — .revue.yml Pattern Support
**Date:** 2026-04-02
**Reviewer:** Bob (SM)
**Score:** 18/20
**Verdict:** FAIL — 2 gaps in Dependencies section must be resolved before dev starts

---

## Detailed Scorecard

### Section 1: Story Structure (7/7)

| # | Criterion | Result | Justification |
|---|-----------|--------|---------------|
| 1 | User Story present and follows format | PASS | "As a Revue user, I want to define allowed and disallowed patterns in `.revue.yml` so that Revue does not flag intentional design decisions as false positives." Correct As a / I want / so that structure. |
| 2 | Background section present (2-4 sentences context) | PASS | Thorough background identifying the four specific recurring false positives from REVUE-83/84/86 review runs. Explains why this matters and what exists today. |
| 3 | Acceptance Criteria present and numbered | PASS | 5 numbered ACs (AC1-AC5) covering schema, injection, config, comparison run, and documentation. |
| 4 | Test Cases section present with named tests | PASS | 10 named test cases with descriptive names and clear descriptions of what each asserts. |
| 5 | Out of Scope section present | PASS | 6 explicit exclusions (DB pattern matching, regex, CRUD UI, auto-detection, CI gate, DB seeding). Well-defined boundaries. |
| 6 | Dependencies section present with table | PASS | 2 dependencies in structured table with Status and Resolution columns. |
| 7 | Dev Notes / Technical Notes present | PASS | Extensive: existing YAML structure, target schema example, system prompt injection format, dual codebase warning (REVUE-102), DB container requirement. |

### Section 2: Acceptance Criteria Quality (3/3)

| # | Criterion | Result | Justification |
|---|-----------|--------|---------------|
| 8 | Each AC is specific and testable | PASS | All ACs have concrete, verifiable conditions (e.g., AC1: "Invalid entries produce a clear validation error at startup with file path and line reference"). |
| 9 | Each AC has clear pass/fail criteria | PASS | Unambiguous outcomes: AC4 specifies "zero findings matching any of the four allowed patterns." AC1 specifies exact validation behavior. |
| 10 | ACs provide complete functional coverage | PASS | Full coverage: schema parsing (AC1), prompt injection (AC2), config population (AC3), validation run (AC4), documentation (AC5). |

### Section 3: Test Coverage (3/3)

| # | Criterion | Result | Justification |
|---|-----------|--------|---------------|
| 11 | Test cases map to acceptance criteria | PASS | Every test case explicitly references its AC (e.g., "test_yaml_parser_reads_allowed_patterns (AC1)"). Full traceability. |
| 12 | Includes positive and negative test cases | PASS | Positive: valid parsing, injection, config verification. Negative: `test_yaml_parser_rejects_invalid_pattern` (missing key). Edge case: `test_empty_patterns_no_injection`. |
| 13 | Test cases describe expected assertions | PASS | Each test describes setup and assertion (e.g., "Load a `.revue.yml` fixture containing two `allowed_patterns` entries. Assert the parser returns a list of two pattern objects with correct `pattern` and `rationale` fields."). |

### Section 4: Dependencies & Blockers (1/3)

| # | Criterion | Result | Justification |
|---|-----------|--------|---------------|
| 14 | Each dependency has a documented status | **FAIL** | REVUE-89 is listed as **"To Do"** but is actually **implementation-complete** (branch `feat/REVUE-89-schema-migration`, all 11 tests passing, completion summary dated 2026-03-31). The dependency status is **stale and incorrect**. REVUE-93's story already references REVUE-89 as "Done". |
| 15 | Each dependency has a resolution/mitigation plan | PASS | Both dependencies have resolution text. REVUE-89 notes parallel dev is possible for AC2. REVUE-102 specifies dual-codebase approach. |
| 16 | No unresolved hard blockers preventing dev start | **FAIL** | REVUE-89 is marked "Must be Done before dev starts" but is **not yet merged to main** (branch exists, PR not created/merged). Until REVUE-89 is merged, REVUE-94 cannot fully start. The schema tables (`allowed_patterns`, `disallowed_patterns`, `finding_pattern_matches`) are not yet on main. |

### Section 5: Planning & Estimation (2/2)

| # | Criterion | Result | Justification |
|---|-----------|--------|---------------|
| 17 | Story is estimated | PASS | 5 story points. |
| 18 | Tasks/subtasks broken down | PASS | 7 tasks with detailed subtasks covering parser, injection, config, comparison run, docs, unit tests, and integration tests. |

### Section 6: Technical Readiness (2/2)

| # | Criterion | Result | Justification |
|---|-----------|--------|---------------|
| 19 | Technical design sufficient for implementation | PASS | Target YAML schema with exact structure, system prompt injection format with example output, dual codebase locations identified. Dev can start coding from these notes. |
| 20 | Edge cases, constraints, and risks identified | PASS | Dual codebase risk (REVUE-102) called out with mitigation. DB container requirement noted. Backward compatibility explicitly addressed. Out-of-scope boundaries prevent scope creep. |

---

## Gaps (Must Fix Before Dev Start)

### Gap 1: Stale dependency status for REVUE-89 (Criterion 14)

**Problem:** The dependency table says REVUE-89 is "To Do", but REVUE-89 implementation is complete (branch `feat/REVUE-89-schema-migration`, commit `1b2c90f`, 11/11 tests passing, completion summary dated 2026-03-31).

**Fix required:** Update the dependency table to reflect REVUE-89's actual status: "Implementation Complete — Awaiting PR Merge."

### Gap 2: REVUE-89 not yet merged to main (Criterion 16)

**Problem:** The story declares `Status: ready-for-dev` and REVUE-89 is listed as "Must be Done before dev starts." However, REVUE-89's branch has not been pushed and no PR has been created or merged. The `allowed_patterns`, `disallowed_patterns`, and `finding_pattern_matches` schema tables are not yet available on main.

**Fix required:** Either:
- **(a)** Merge REVUE-89 to main first, then mark REVUE-94 as ready-for-dev, OR
- **(b)** Revise the dependency resolution to explicitly state that AC2 (prompt injection) development can begin on a branch based on `feat/REVUE-89-schema-migration`, with AC4 (comparison run) gated on REVUE-89 merge. Update story status accordingly.

---

## Summary

The story itself is **exceptionally well-written** — clear user story, thorough background, specific and testable ACs, comprehensive test cases with AC traceability, well-defined scope boundaries, and detailed technical notes. The only issues are in dependency management: a stale status and an unmerged hard prerequisite. These are procedural fixes, not story quality problems.

**Recommended path to PASS:** Push and merge the REVUE-89 branch, update the REVUE-94 dependency table, then re-run this DoR gate.

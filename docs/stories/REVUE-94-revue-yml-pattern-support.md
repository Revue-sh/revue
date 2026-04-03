# REVUE-94 — .revue.yml Pattern Support

**Status:** ready-for-dev
**Epic:** REVUE-87
**Estimate:** 5 points
**Priority:** Medium

---

## User Story

As a Revue user, I want to define allowed and disallowed patterns in `.revue.yml` so that Revue does not flag intentional design decisions as false positives.

## Background

Across review runs for REVUE-83, REVUE-84, and REVUE-86, four recurring false positives were identified that stem from intentional design decisions in the revue.io codebase:

1. **`_def` attribute access on `LoadedAgent`** — flagged as accessing a private/internal attribute, but this is intentional because `LoadedAgent` has no public API for definition access.
2. **Inline lazy `httpx` import in `pr_description_adapter`** — flagged as a non-standard import pattern. Originally intentional for lazy loading; has since been replaced with a module-level import.
3. **`test_vcs_adapter.py` deletion** — flagged as removing test coverage, but the tests were consolidated into `test_vcs_adapters.py` (plural), so coverage still exists.
4. **Bare `except` in `_inject_pr_context`** — flagged as overly broad exception handling, but this is intentional because the PR context injection must never crash the review loop.

These false positives add noise to every review, reducing trust in findings and wasting reviewer time triaging known patterns. The `.revue.yml` config file already has a `noise_filters` section (currently only `disable` and `low_confidence_threshold`). This story extends it with `allowed_patterns` and `disallowed_patterns`, injects them into agent system prompts, and populates the revue.io project config with the four known patterns above. The REVUE-89 schema includes `allowed_patterns` and `disallowed_patterns` tables plus a `finding_pattern_matches` junction table for tracking which findings matched which patterns.

## Acceptance Criteria

**AC1 — `.revue.yml` schema extended with `noise_filters.allowed_patterns` and `noise_filters.disallowed_patterns`:**
The YAML parser recognises two new keys under `noise_filters`: `allowed_patterns` (list of objects, each with `pattern` string and `rationale` string) and `disallowed_patterns` (same structure). Invalid entries (missing `pattern` key, non-string values) produce a clear validation error at startup with file path and line reference. Existing `.revue.yml` files without these keys continue to work without errors (backward compatible).

**AC2 — Patterns injected into agent system prompts before review:**
When the review agent is initialised, all `allowed_patterns` from `.revue.yml` are appended to the agent system prompt under a clearly delimited section (e.g., `## Allowed Patterns — Do Not Flag`). Each entry includes the pattern text and its rationale. Similarly, `disallowed_patterns` are appended under a `## Disallowed Patterns — Always Flag` section. The injection happens before the first LLM call, ensuring the agent sees the patterns for every finding it generates.

**AC3 — `revue.io/.revue.yml` populated with the four known false-positive patterns:**
The project's own `.revue.yml` is updated with four `allowed_patterns` entries under `noise_filters`:
1. `_def attribute access on LoadedAgent` — rationale: "Internal implementation detail, no public API"
2. `Inline lazy httpx import in pr_description_adapter` — rationale: "Intentional lazy loading pattern, now replaced with module-level import"
3. `test_vcs_adapter.py deletion` — rationale: "Test coverage consolidated into test_vcs_adapters.py"
4. `Bare except in _inject_pr_context` — rationale: "Intentional catch-all, PR context injection must not crash the review loop"

**AC4 — Comparison run shows reduction in false positives for known patterns:**
A before/after comparison run is executed on a branch or commit known to trigger the four false positives. The "before" run uses the existing `.revue.yml` (no patterns). The "after" run uses the updated `.revue.yml` with the four `allowed_patterns`. The after run produces zero findings matching any of the four allowed patterns. Results are logged or captured as evidence (e.g., JSON diff or comparison summary in the PR description).

**AC5 — Customer documentation updated:**
`README.md` is updated to mention the `noise_filters.allowed_patterns` and `noise_filters.disallowed_patterns` configuration options with a brief example. `docs/configuration.md` is updated (or created if it doesn't exist) with the full schema reference for both pattern types, including field descriptions, example YAML, and a note on how patterns interact with agent prompts.

## Test Cases

**test_yaml_parser_reads_allowed_patterns** (AC1):
- Load a `.revue.yml` fixture containing two `allowed_patterns` entries.
- Assert the parser returns a list of two pattern objects with correct `pattern` and `rationale` fields.

**test_yaml_parser_reads_disallowed_patterns** (AC1):
- Load a `.revue.yml` fixture containing one `disallowed_patterns` entry.
- Assert the parser returns a list with one pattern object.

**test_yaml_parser_backward_compatible** (AC1):
- Load a `.revue.yml` fixture with no `allowed_patterns` or `disallowed_patterns` keys.
- Assert the parser returns empty lists for both (no error raised).

**test_yaml_parser_rejects_invalid_pattern** (AC1):
- Load a `.revue.yml` fixture with a pattern entry missing the `pattern` key.
- Assert a validation error is raised with a descriptive message.

**test_allowed_patterns_injected_into_system_prompt** (AC2):
- Configure two allowed patterns in `.revue.yml`.
- Initialise the review agent.
- Assert the agent's system prompt contains `## Allowed Patterns — Do Not Flag` followed by both pattern texts and rationales.

**test_disallowed_patterns_injected_into_system_prompt** (AC2):
- Configure one disallowed pattern in `.revue.yml`.
- Initialise the review agent.
- Assert the agent's system prompt contains `## Disallowed Patterns — Always Flag` followed by the pattern text and rationale.

**test_empty_patterns_no_injection** (AC2):
- Configure `.revue.yml` with empty `allowed_patterns` and `disallowed_patterns`.
- Initialise the review agent.
- Assert the system prompt does NOT contain the pattern section headers.

**test_revue_yml_contains_four_allowed_patterns** (AC3):
- Read the project's `.revue.yml` file.
- Assert `noise_filters.allowed_patterns` contains exactly four entries.
- Assert each entry has both `pattern` and `rationale` fields populated.
- Assert the four known false-positive patterns are present by pattern text.

**test_comparison_run_fp_reduction** (AC4):
- Run a review against a known-FP fixture without patterns.
- Run the same review with the four allowed patterns configured.
- Assert the second run produces zero findings matching any of the four allowed pattern texts.

**test_docs_configuration_updated** (AC5):
- Assert `docs/configuration.md` exists and contains the strings `allowed_patterns` and `disallowed_patterns`.
- Assert `README.md` contains a reference to `noise_filters` pattern configuration.

## Out of Scope

- **Pattern matching against DB tables at review time** — this story injects patterns into system prompts only. Writing pattern matches to the `finding_pattern_matches` DB table is a downstream integration (requires REVUE-90 import pipeline).
- **Regex or glob-based pattern matching** — patterns are natural-language descriptions injected into LLM prompts, not programmatic matchers.
- **Pattern CRUD UI or CLI** — patterns are edited directly in `.revue.yml`. No interactive management interface.
- **Auto-detection of new false-positive candidates** — this story only handles manually-defined patterns.
- **`disallowed_patterns` enforcement at CI gate** — disallowed patterns inform the agent to flag harder, but do not block merges or fail CI.
- **DB seeding of patterns** — populating the `allowed_patterns` / `disallowed_patterns` DB tables from `.revue.yml` is deferred to the REVUE-90 import integration.

## Dependencies

| Dependency | Status | Resolution |
|---|---|---|
| REVUE-89 (Knowledge Base Schema) | Done (PR #22 merged to main) | Schema complete. Provides `allowed_patterns`, `disallowed_patterns`, and `finding_pattern_matches` tables. Ready for AC4 comparison run and DB tracking. |
| REVUE-102 (Retire AIReviewer / Consolidate Codebase) | Done in Jira, NOT implemented in code | Dual codebase is still active. Any changes to shared modules (e.g., YAML config parser, agent initialisation) must be applied to BOTH `src/revue/core/` AND `src/AIReviewer/core/`. Dev agent must verify which codebase paths are affected and apply changes to both until REVUE-102 consolidation is actually completed. |

## Dev Notes

**Existing `.revue.yml` structure:**
The config file already has a `noise_filters` section with `disable` (list) and `low_confidence_threshold` (float). The new `allowed_patterns` and `disallowed_patterns` keys are added as siblings under `noise_filters`.

**Target YAML schema:**
```yaml
noise_filters:
  disable: []
  low_confidence_threshold: 0.5
  allowed_patterns:
    - pattern: "_def attribute access on LoadedAgent"
      rationale: "Internal implementation detail, no public API"
    - pattern: "Inline lazy httpx import in pr_description_adapter"
      rationale: "Intentional lazy loading pattern, now replaced with module-level import"
    - pattern: "test_vcs_adapter.py deletion"
      rationale: "Test coverage consolidated into test_vcs_adapters.py"
    - pattern: "Bare except in _inject_pr_context"
      rationale: "Intentional catch-all, PR context injection must not crash the review loop"
  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as Jira tickets"
```

**System prompt injection format:**
```
## Allowed Patterns — Do Not Flag
The following patterns represent intentional design decisions. Do NOT report findings for these:
- _def attribute access on LoadedAgent — Internal implementation detail, no public API
- ...

## Disallowed Patterns — Always Flag
The following patterns should always be reported, regardless of confidence:
- TODO comments in production code — TODOs should be tracked as Jira tickets
- ...
```

**Dual codebase warning (REVUE-102):**
Check both `src/revue/core/` and `src/AIReviewer/core/` for the YAML config parser and agent system prompt construction. Apply changes to both locations until the codebase consolidation is complete.

**DB container requirement:**
The `revue-db` Postgres container must be running for the comparison run (AC4). Connection via `DATABASE_URL` env var from `~/.zshenv`.

## Tasks/Subtasks

- [ ] Task 1: Extend `.revue.yml` parser for pattern support
  - [ ] Add `allowed_patterns` and `disallowed_patterns` parsing to YAML config loader
  - [ ] Add validation (each entry must have `pattern` string and `rationale` string)
  - [ ] Ensure backward compatibility (missing keys default to empty lists)
  - [ ] Apply to both `src/revue/core/` and `src/AIReviewer/core/` if config parser exists in both
- [ ] Task 2: Inject patterns into agent system prompts
  - [ ] Build prompt section from allowed patterns list
  - [ ] Build prompt section from disallowed patterns list
  - [ ] Inject sections before first LLM call in agent initialisation
  - [ ] Skip injection when both lists are empty (no empty headers)
  - [ ] Apply to both codebases if agent init exists in both
- [ ] Task 3: Populate `.revue.yml` with four known false-positive patterns
  - [ ] Add four `allowed_patterns` entries to project `.revue.yml`
  - [ ] Add one example `disallowed_patterns` entry (TODO comments)
- [ ] Task 4: Run before/after comparison
  - [ ] Execute baseline review run (without patterns)
  - [ ] Execute review run with patterns enabled
  - [ ] Capture and document FP reduction evidence
- [ ] Task 5: Update customer documentation
  - [ ] Update `README.md` with pattern configuration mention
  - [ ] Create or update `docs/configuration.md` with full schema reference
- [ ] Task 6: Write unit tests
  - [ ] `test_yaml_parser_reads_allowed_patterns`
  - [ ] `test_yaml_parser_reads_disallowed_patterns`
  - [ ] `test_yaml_parser_backward_compatible`
  - [ ] `test_yaml_parser_rejects_invalid_pattern`
  - [ ] `test_allowed_patterns_injected_into_system_prompt`
  - [ ] `test_disallowed_patterns_injected_into_system_prompt`
  - [ ] `test_empty_patterns_no_injection`
  - [ ] `test_revue_yml_contains_four_allowed_patterns`
- [ ] Task 7: Write integration/E2E tests
  - [ ] `test_comparison_run_fp_reduction`
  - [ ] `test_docs_configuration_updated`

---

## Dev Agent Record

### Implementation Plan
TDD within each task group: write tests first (RED), implement to GREEN, commit.
Dual codebase changes applied to both src/revue/core/ and src/AIReviewer/core/.
7 commits matching 7 task groups in the story.

### Debug Log
No issues encountered. All tests passed on first GREEN attempt.

### Completion Notes
All 5 ACs verified. 1018 + 98 tests pass. See `REVUE-94-completion-summary.md` for full DoD scorecard.

## File List
See `REVUE-94-completion-summary.md` for complete file listing.

## Change Log
- 2026-04-02: Implementation complete (Amelia). 7 commits, all tests green.

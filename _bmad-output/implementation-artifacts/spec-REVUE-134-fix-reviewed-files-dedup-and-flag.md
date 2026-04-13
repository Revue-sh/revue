---
title: 'Fix: Deduplicate reviewed files list + show_reviewed_files flag'
type: 'bugfix'
created: '2026-04-13'
status: 'done'
jira: 'REVUE-134'
baseline_commit: '3834af5ef06d121f9bbf67265b35c27e9f2efa02'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The "Files Reviewed" section of the Revue summary comment lists the same file path multiple times — once per agent-file review result — because `review_results` has one entry per agent-file pair, not per unique file. The count (e.g., "Files Reviewed (25)") is therefore inflated and the list is visually noisy.

**Approach:** Deduplicate the file list by path (preserving insertion order) and make the section opt-out via a `features.show_reviewed_files` flag in `.revue.yml` (default `true`, so existing behaviour is preserved unless the user explicitly disables it).

## Boundaries & Constraints

**Always:**
- Default for `show_reviewed_files` is `true` — no change in behaviour for users who do not touch their config.
- Deduplication preserves insertion order (first occurrence wins).
- The count in the heading must reflect the number of unique files shown, not the raw `len(review_results)`.
- Every code-path that calls `_build_enhanced_summary` must honour the flag.

**Ask First:**
- If the desired default for `show_reviewed_files` changes (e.g., ship with `false` by default), halt and confirm before writing.

**Never:**
- Do not remove the "Files Reviewed" section unconditionally — it must remain toggleable.
- Do not thread the full `AIConfig` object into `_build_enhanced_summary`; pass the single boolean flag only (SRP).
- Do not change any other summary sections (quality breakdown, findings, verdict).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Duplicate file paths in results | 3 `ReviewResult` entries for `foo.py`, 1 for `bar.py` | Section shows `foo.py` once + `bar.py` once; count = 2 | N/A |
| Flag disabled | `show_reviewed_files=False` passed to builder | No "### Files Reviewed" heading in output | N/A |
| Flag enabled (default) | `show_reviewed_files=True` (default) | Section present as before | N/A |
| All results errored | All `rr.error=True` | Deduplication produces empty list; section shows "Files Reviewed (0)" | N/A |

</frozen-after-approval>

## Code Map

- `src/revue/cli.py:402` -- `_build_enhanced_summary` — where the "Files Reviewed" section is built (lines 488–493); add `show_reviewed_files` param + dedup logic
- `src/revue/cli.py:821` -- `_post_to_platform` — add `show_reviewed_files: bool = True` param; thread it to `_build_enhanced_summary` at two call sites (lines 964, 1004)
- `src/revue/cli.py:1014,1051,1092` -- `_post_to_bitbucket`, `_post_to_github`, `_post_to_gitlab` — read `config.show_reviewed_files` (default `True`) and pass to `_post_to_platform`
- `src/revue/core/ai_config.py:69` -- `AIConfig` features section — add `show_reviewed_files: bool = True`
- `src/revue/core/config_loader.py:181` -- `features` section parsing — add `if "show_reviewed_files" in features: config.show_reviewed_files = bool(...)`
- `src/revue/core/config_loader.py:54` -- `DEFAULT_REVUE_YML` template — add `show_reviewed_files: true` under `features:`
- `src/revue/tests/core/test_pipeline.py:1084` -- where TC12 lives — add TC14, TC15, TC16 after it

## Tasks & Acceptance

**Execution:**
- [ ] `src/revue/tests/core/test_pipeline.py` -- ADD tests TC14–TC16 (write failing tests first, per TDD) -- prove dedup and flag behaviour before any implementation
- [ ] `src/revue/core/ai_config.py` -- ADD `show_reviewed_files: bool = True` field in the features section (after `preserve_comment_threads`) -- makes flag available on config
- [ ] `src/revue/core/config_loader.py` -- ADD `features.show_reviewed_files` parsing; add `show_reviewed_files: true  # Show reviewed-files list in summary comment` to `DEFAULT_REVUE_YML` under `features:` -- wires YAML → AIConfig
- [ ] `src/revue/cli.py` -- UPDATE `_build_enhanced_summary`: add `show_reviewed_files: bool = True` param; replace lines 489–493 with dedup + conditional render -- core fix
- [ ] `src/revue/cli.py` -- UPDATE `_post_to_platform`: add `show_reviewed_files: bool = True` param; pass it to both `_build_enhanced_summary` call sites -- threads flag through posting logic
- [ ] `src/revue/cli.py` -- UPDATE `_post_to_bitbucket`, `_post_to_github`, `_post_to_gitlab`: read `getattr(config, "show_reviewed_files", True)` and pass to `_post_to_platform` -- connects config → platform posting

**Acceptance Criteria:**
- Given a PR with 3 agent results for `service.py` and 1 for `pipeline.py`, when the summary is built, then "Files Reviewed (2)" is shown with each path listed exactly once.
- Given `show_reviewed_files: false` in `.revue.yml features`, when a review is posted, then the summary comment contains no "### Files Reviewed" heading.
- Given no `show_reviewed_files` key in `.revue.yml` (or default config), when a review is posted, then the "### Files Reviewed" section is present (backward-compatible default).
- Given `features.show_reviewed_files: true` is parsed from YAML, when `load_config` runs, then `config.show_reviewed_files == True`.

## Spec Change Log

## Design Notes

**Deduplication approach:** Use `dict.fromkeys(rr.file_path for rr in reviewed_results)` to get an ordered, unique sequence of paths in a single pass — no sorting, preserves first-occurrence order.

```python
reviewed_files = [rr for rr in review_results if not rr.error and rr.response]
unique_paths = list(dict.fromkeys(rr.file_path for rr in reviewed_files))
lines.append(f"### Files Reviewed ({len(unique_paths)})")
for path in unique_paths:
    lines.append(f"- `{path}`")
```

## Verification

**Commands:**
- `cd /Volumes/LexarSSD/Projects/revue.io/src && PYTHONPATH=$(pwd) pytest revue/tests/core/test_pipeline.py -v -k "TC14 or TC15 or TC16"` -- expected: all 3 pass
- `cd /Volumes/LexarSSD/Projects/revue.io/src && PYTHONPATH=$(pwd) pytest revue/tests/ -q` -- expected: full suite green, no regressions

## Suggested Review Order

**Core fix — dedup and flag guard**

- Entry point: `if show_reviewed_files:` guard + `dict.fromkeys` dedup replacing the old list loop
  [`cli.py:488`](../../src/revue/cli.py#L488)

- Signature change: new `show_reviewed_files: bool = True` param added last
  [`cli.py:405`](../../src/revue/cli.py#L405)

**Flag propagation — config → wrappers → platform → builder**

- `_post_to_platform` receives the flag and threads it to both `_build_enhanced_summary` call sites
  [`cli.py:833`](../../src/revue/cli.py#L833)

- Bitbucket wrapper reads `getattr(config, "show_reviewed_files", True)` and passes down
  [`cli.py:1039`](../../src/revue/cli.py#L1039)

- GitHub wrapper — same pattern
  [`cli.py:1085`](../../src/revue/cli.py#L1085)

- GitLab wrapper — same pattern
  [`cli.py:1128`](../../src/revue/cli.py#L1128)

**Config plumbing — field, YAML parser, template**

- `AIConfig` field: `show_reviewed_files: bool = True` in the feature-flags block
  [`ai_config.py:70`](../../src/revue/core/ai_config.py#L70)

- Parser: `features.show_reviewed_files` → `config.show_reviewed_files`
  [`config_loader.py:182`](../../src/revue/core/config_loader.py#L182)

- Template: `show_reviewed_files: true` added to `DEFAULT_REVUE_YML` under `features:`
  [`config_loader.py:74`](../../src/revue/core/config_loader.py#L74)

**Tests**

- TC14 dedup assertion, TC15 flag-disabled, TC16 flag-default — three new tests
  [`test_pipeline.py:1290`](../../src/revue/tests/core/test_pipeline.py#L1290)

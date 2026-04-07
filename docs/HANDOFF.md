# Session Handoff - 2026-04-07
**Duration:** ~4h | **Agent:** Amelia (bmad-agent-dev)

## Session Summary
This session continued REVUE-110 (`feat/REVUE-110-duplicate-comments-fix`) after the previous
handoff. Five commits were landed: rate-limit error handling, partial-failure pipeline exit,
GitLab `line_code` computation for inline comments, and Anthropic Prompt Caching to eliminate
re-review 429 rate limits. All 673 tests pass; branch is pushed to all three remotes.

## Project Status
| Metric | Value |
|--------|-------|
| Stories complete | REVUE-110, REVUE-111 in review |
| Tests passing | 673 |
| Open PRs | Bitbucket PR #36, GitHub PR, GitLab MR #2 (feat/REVUE-110) |
| Branch | feat/REVUE-110-duplicate-comments-fix |

## Completed this session
- `dd3a080` feat(rate-limit): fail-fast 429 errors with visible `RATE LIMIT ERROR` block +
  `retry_on_rate_limit` config opt-in; all 5 AI clients unified
- `b04334c` fix(pipeline): fail CI on partial agent failures (not just all-fail); 400 debug logging
  added to GitLab adapter; pipeline returns 4-tuple with `failed_agents`
- `4f7d746` fix(logging): consistent failure structure between GitLab (partial) and Bitbucket (all)
  log formats; `_short_error()` helper; `AllAgentsFailedError` unified to stdout
- `3dd35e8` fix(gitlab): implement `compute_gitlab_line_code()` in `vcs_adapter.py` - real
  SHA1-based `{hash}_{old}_{new}` format + diff parsing + out-of-hunk snapping; fixes HTTP 400
  "line_code can't be blank" errors
- `448a77e` feat(cache): Anthropic + OpenAI Prompt Caching (attempted) - `cache_control: ephemeral`
  on system prompt + diff blocks (Anthropic explicit); `_openai_messages()` restructures for OpenAI
  automatic prefix caching + strips `cache_control` to avoid 422; all 5 clients updated

## What We Built (Session Highlights)

### Rate Limit Handling - REVUE-110 fix
`ai_client.py`: All 5 clients use `self._max_attempts = 3 if retry_on_rate_limit else 1`.
Default is fail-fast with a visible error block naming the reason. `_with_retry()` prefers
`retry-after` header for backoff delay. `.revue.yml` now has `retry_on_rate_limit: false`.

### Partial Agent Failure - REVUE-110 fix
`pipeline.py`: `run()` now returns 4-tuple `(results, excluded, files_reviewed, failed_agents)`.
CLI unpacks it and exits 1 if `failed_agents` non-empty, after posting partial findings.
All pipeline tests updated to unpack 4-tuple.

### GitLab line_code Fix - REVUE-110 AC
`vcs_adapter.py`: Added `_map_diff_lines()` and `compute_gitlab_line_code()`. Parses `@@ -old +new @@`
hunks, maps new-line -> old-line, computes `sha1(file_path)[0:8]_{old}_{new}`. Snaps lines
outside hunks to nearest valid position. Fixes all HTTP 400 errors on inline GitLab comments.

### Anthropic + OpenAI Prompt Caching - REVUE-110 performance fix (attempted)
Root cause from CSV: `usage_input_tokens_cache_write_5m: 0`, `cache_read: 0` — zero cache
usage on every call. Every token sent cold. 4 agents x 8k tokens = 32k effective TPM vs 30k limit.

**Anthropic (explicit):** `agent_loader.py` detects `AnthropicClient` and builds structured call:
- `system` = `[{text: system_prompt, cache_control: ephemeral}]`
- `user[0]` = `{text: diff_text, cache_control: ephemeral}` (cacheable per PR across agents)
- `user[1]` = `{text: shared_context + instructions}` (not cached - dynamic)

`shared_analysis.py`: Same split for the orchestrator call. Target: re-reviews drop from
~32k to ~3.2k effective TPM (cached tokens count at 10% rate).

**OpenAI/Azure/OpenRouter/Custom (structural - relies on automatic prefix caching):**
`_openai_messages()` helper restructures messages: system prompt prepended as `role: system`
message first (static, large - triggers OpenAI automatic prefix caching for GPT-4o+ at
>1024 tokens), then strips `cache_control` fields (OpenAI API rejects them with 422).
Not explicitly configured - relies on OpenAI's automatic caching activating.

All 5 `complete()` signatures now accept `system: str | list[dict] | None = None`.
Status: **implemented, not yet confirmed** - next CI run with cache CSV will show
non-zero `cache_write_5m` / `cache_read` columns for Anthropic; OpenAI caching is opaque.

## Remaining Work - Next Steps

1. **Monitor CI pipelines** (top priority, unblocked)
   - First action: check GitLab job on `feat/REVUE-110-duplicate-comments-fix` for 0 HTTP 400 errors
   - Check that cache columns are non-zero in next Claude usage CSV
     (`usage_input_tokens_cache_write_5m`, `usage_input_tokens_cache_read`)
   - URL: https://gitlab.com/urukia-group/revue-test-gitlab/-/pipelines

2. **Merge REVUE-110 to main** (after CI green)
   - First action: merge Bitbucket PR #36 -> main
   - Then: `git push github main && git push gitlab main`
   - Transition Jira REVUE-110 to Done (after Daniel confirms E2E pass)

3. **Winston findings from party-mode review** (8 open items on REVUE-110 PR)
   - Finding 5 (`fingerprint.py:34`) is O(n*m) - worth fixing before merge or as quick follow-up
   - Finding 4 and 7 (bare `except Exception`) - tighten before merge
   - CI caching (finding 1/2) - low priority, delegate to REVUE-111 or separate ticket
   - First action: read `src/revue/comments/fingerprint.py:34` and `src/revue/cli.py:792`

4. **Next story: [68] Conversion analytics dashboard** (E6 active backlog)
   - First action: create story file in `docs/stories/REVUE-[next].md` from PRD/kanban
   - Alternative: [71] Nuitka build pipeline if higher priority

## Key Architectural Decisions (Session)

1. **Caching: system prompt + diff block cached, instructions not cached** - The diff block is
   `ephemeral` (5-min TTL) which covers multi-agent parallel runs on the same diff. Instructions
   block varies per agent so deliberately not marked cacheable.

2. **`_openai_messages()` strips `cache_control` before OpenAI APIs** - Anthropic-specific key
   would cause 422 rejection from OpenAI-compatible endpoints. Stripping is transparent to callers.

3. **4-tuple return from `pipeline.run()`** - Adding `failed_agents` as 4th element rather than
   raising an exception preserves partial results (findings still posted even when some agents fail).

4. **GitLab line snapping to nearest hunk** - When a finding line is outside all diff hunks
   (e.g. a summary comment on line 1 of an unchanged file), we snap to the nearest valid hunk line
   rather than returning an error. This prevents silent failures on context-only findings.

## Session Stats
- Duration: ~4h
- Stories: REVUE-110 implementation complete (awaiting CI confirmation)
- Commits: 5
- Tests: 673 passing
- PRs: Bitbucket #36, GitHub mirror PR, GitLab MR #2
- Party mode agents used: Winston (architect review)

## Continuation Prompt (Next Session)
REVUE-110 (`feat/REVUE-110-duplicate-comments-fix`) is complete and pushed to all 3 remotes (5
commits this session). Read `docs/HANDOFF.md` first. Awaiting CI green on GitLab to confirm:
(1) no more HTTP 400 line_code errors, (2) no 429 rate limits. Key unverified item: Anthropic
Prompt Caching (commit 448a77e) - check next Claude usage CSV for non-zero cache_write_5m /
cache_read columns; OpenAI automatic prefix caching is opaque. If CI is green: merge Bitbucket
PR #36 -> main, push main to github/gitlab remotes, transition Jira REVUE-110 to Done. If CI
shows failures, paste the log. Winston's open findings: fingerprint.py:34 (O(n*m)) + cli.py:792
(bare except) worth fixing - check before merge.

# Session Handoff - 2026-04-07
**Duration:** ~3h | **Agent:** BMad Master (party mode: Winston, Amelia, Bob)

## Session Summary
This session fixed the broken Anthropic Prompt Caching from commit 448a77e (per-block
cache_control was silently ignored because agent system prompts are below the 1,024-token
minimum), implemented proper top-level cache_control via SDK 0.87.0's native parameter
(REVUE-115), then added OpenAI prompt caching observability and prompt_cache_key routing
for re-review cache hits (REVUE-116). All changes ride the existing REVUE-110 branch.
682 tests pass; all 3 pipeline files updated with debug logging to confirm caching in CI.

## Project Status
| Metric | Value |
|--------|-------|
| Stories complete | REVUE-115 done, REVUE-116 done (both In Progress in Jira) |
| Tests passing | 682 |
| Open PRs | Bitbucket PR #36, GitHub PR, GitLab MR #2 (feat/REVUE-110) |
| Branch | feat/REVUE-110-duplicate-comments-fix |

## Completed this session

- `6df420d` feat(cache)[REVUE-115]: top-level cache_control in AnthropicClient + observability
  - Replaced per-block cache_control (448a77e) with SDK-native top-level `cache_control={"type":"ephemeral"}`
  - Removed Anthropic-specific branches from agent_loader.py and shared_analysis.py entirely
  - AnthropicClient.complete() now logs cache_creation_input_tokens + cache_read_input_tokens
  - 4 new tests (TC1-TC4); 677 passing

- `e8d1f04` feat(cache)[REVUE-116]: OpenAI cached_tokens logging + prompt_cache_key routing
  - _log_openai_usage() helper logs usage.prompt_tokens_details.cached_tokens for all 4 OpenAI clients
  - AIClient protocol gains `cache_key: str | None = None`; OpenAI clients forward as prompt_cache_key
  - LoadedAgent.analyse() + run_shared_analysis() compute SHA256[:16] of diff as cache_key
  - 5 new tests (TC1-TC5); 682 passing

- `02eaee0` chore(ci): --log-cli-level=DEBUG to pytest + REVUE_LOG_LEVEL=DEBUG to review steps
  - cli.py reads REVUE_LOG_LEVEL env var (default WARNING) via logging.basicConfig()
  - Bitbucket, GitLab, GitHub Actions all updated

- Story files created: docs/stories/REVUE-115.md, docs/stories/REVUE-116.md
- Jira tickets created: REVUE-115 (In Progress), REVUE-116 (In Progress)

## What We Built (Session Highlights)

### REVUE-115 - Anthropic Prompt Caching Fix
Root cause: agent system prompts (~400-600 tokens) are below the 1,024-token minimum for
Sonnet 4.6. Per-block cache_control was silently ignored. Fix: SDK 0.87.0 supports
`cache_control={"type":"ephemeral"}` as a top-level parameter on messages.create() (confirmed
via inspect.signature). AnthropicClient.complete() now passes it at the top level; the SDK
auto-determines the last cacheable block. Callers (agent_loader.py, shared_analysis.py) are
now clean - no provider-specific branching. Cache debug log added to resp.usage readback.

### REVUE-116 - OpenAI Prompt Caching Observability + Routing
OpenAI caching is automatic (no cache_control needed). Two gaps filled:
1. Observability: usage.prompt_tokens_details.cached_tokens now logged at DEBUG for all 4
   OpenAI-compatible clients (OpenAI, Azure, OpenRouter, Custom).
2. Routing: prompt_cache_key (SHA256[:16] of diff content) passed to chat.completions.create()
   so re-reviews of the same PR are routed to the same cache server. Anthropic ignores cache_key.
SDK 2.30.0 (installed) supports prompt_cache_key as a native chat.completions.create() parameter.

### CI Cache Verification
All 3 pipeline files updated. Expected log lines after next CI run:
  DEBUG revue.core.ai_client [anthropic] cache_creation=N cache_read=N input=N output=N
  DEBUG revue.core.ai_client [openai] cached=N prompt=N completion=N
cache_creation > 0 on first review; cache_read > 0 on re-review = caching confirmed.

## Remaining Work - Next Steps

1. **Monitor CI pipelines** (top priority - first action)
   - Trigger/check GitLab MR pipeline on feat/REVUE-110-duplicate-comments-fix
   - Look for DEBUG lines in the revue-review step output confirming cache_creation > 0
   - URL: https://gitlab.com/urukia-group/revue-test-gitlab/-/pipelines
   - If confirmed: merge Bitbucket PR #36 -> main, push to github/gitlab, close REVUE-110

2. **Merge REVUE-110 to main** (after CI green)
   - First action: merge Bitbucket PR #36 -> main
   - Then: `git push github main && git push gitlab main`
   - Transition REVUE-110, REVUE-115, REVUE-116 to Done in Jira

3. **Winston open findings on REVUE-110 PR** (pre-merge cleanup)
   - fingerprint.py:34 (O(n*m) loop) - worth fixing before merge
   - cli.py:792 (bare except) - tighten before merge
   - First action: read src/revue/comments/fingerprint.py:34

4. **Remove REVUE_LOG_LEVEL=DEBUG from pipelines** (after caching confirmed)
   - Once cache_creation/cache_read are confirmed in CI logs, revert to WARNING default
   - First action: edit bitbucket-pipelines.yml, .gitlab-ci.yml, .github/workflows/revue-review.yml

5. **Next backlog story** (after merge)
   - REVUE-112: Won't-fix reply tracking (To Do in Jira)
   - REVUE-113: .revue.yml institutional memory / Nova AI pattern enforcement
   - REVUE-114: .revue/comments/ cleanup on PR lifecycle end

## Key Architectural Decisions (Session)

1. **Top-level cache_control beats per-block** - SDK 0.87.0 messages.create() accepts
   cache_control as a first-class parameter. The SDK auto-selects the cacheable boundary.
   No callers need to know about caching at all.

2. **1,024-token minimum (not 2,048)** - The official Anthropic prompt caching cookbook
   specifies 1,024 tokens for Sonnet 4.6. Earlier notes in commit 448a77e cited 2,048 incorrectly.
   Agent system prompts (~400-600 tokens) are still below even the 1,024 threshold when alone,
   but the combined prefix (system + diff) exceeds it for typical real-world PRs.

3. **cache_key = SHA256[:16] of diff content** - Stable 16-char hex routing key per diff.
   All agents reviewing the same diff use the same cache_key, improving OpenAI server routing
   for re-reviews. Does not help parallel agents on a fresh review (different system prompt
   prefixes = different cache keys regardless).

4. **REVUE_LOG_LEVEL env var pattern** - cli.py reads it via logging.basicConfig() at startup.
   Default WARNING keeps production quiet. CI sets DEBUG explicitly. Safe to remove after
   caching is confirmed working.

## Session Stats
- Duration: ~3h
- Stories: REVUE-115, REVUE-116 implemented (In Progress in Jira)
- Commits: 3 (6df420d, e8d1f04, 02eaee0)
- Tests: 682 passing (+9 this session)
- PRs: existing Bitbucket #36, GitHub PR, GitLab MR #2 (no new PRs opened)
- Party mode agents used: Winston (architect), Amelia (dev), Bob (scrum master)

## Continuation Prompt (Next Session)
Branch feat/REVUE-110-duplicate-comments-fix has 3 new commits this session (REVUE-115
Anthropic cache fix, REVUE-116 OpenAI caching, CI debug logging). Read docs/HANDOFF.md.
First action: check GitLab pipeline for DEBUG lines confirming cache_creation > 0 (look in
revue-review step output). If CI green and caching confirmed: merge Bitbucket PR #36 -> main,
push to github/gitlab remotes, transition REVUE-110/115/116 to Done in Jira. Then fix
fingerprint.py:34 (O(n*m)) and cli.py:792 (bare except) before or after merge. Next backlog
story is REVUE-112 (won't-fix reply tracking) or REVUE-113 (Nova pattern enforcement).

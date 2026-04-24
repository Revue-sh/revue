# Session Handoff - 2026-04-16
**Duration:** ~6h | **Agent:** Claude Sonnet 4.6

## Session Summary

Full implementation session for REVUE-151 (D1 cache prompt restructure, 6pts).
Committed all prior session's uncommitted docs/planning artifacts to a branch, created
.worktrees/ inside the repo (worktree placement policy corrected), implemented all 5 tasks
of REVUE-151 including a critical Sonnet bridge-phrase discovery, and validated against 3
real diffs with AC8-AC9 PASS verdict. ADR status updated Proposed -> Accepted.

## Project Status

| Metric | Value |
|--------|-------|
| Stories complete | REVUE-151 implemented (branch, not merged yet) |
| Tests passing | 875 (869 original + 6 new TC_D1_* tests) |
| Open PRs | None (SSH push blocked at session end) |
| Active worktrees | .worktrees/REVUE-151-cache-d1 (ready for PR), .worktrees/REVUE-113-revue-yml-patterns (not started) |
| Pending branches | docs/REVUE-150-sprint-planning (d09afb8, local only -- push blocked) |

## Completed this session

- Worktree policy corrected: worktrees now placed inside repo at .worktrees/ (not as siblings)
  Updated memory: feedback_worktrees.md
- docs/REVUE-150-sprint-planning branch: committed 10 files (ADRs, story files, jira_transition.sh)
  Commit d09afb8 -- SSH push to Bitbucket failed, branch is local only
- REVUE-151 Task 1: Updated TC1 to RED then GREEN (new D1 contract -- caller owns cache_control)
- REVUE-151 Task 2: Removed _anthropic_messages_with_cache() + auto-append from AnthropicClient
  Commit b3d781a -- 871 tests GREEN. TC_D1_1, TC_D1_2 added.
- REVUE-151 Task 3: Restructured LoadedAgent.analyse() -- diff in system[0] with cache_control
  Commit c4d08a7 -- 874 tests GREEN. TC_D1_3, TC_D1_4, TC_D1_5 added.
- REVUE-151 Task 4: Restructured run_shared_analysis() -- diff_summary in system[0]
  Commit fbe7432 -- 875 tests GREEN. TC_D1_6 added.
- REVUE-151 Fix: Discovered Sonnet treats system-block content as background context --
  won't analyze it without bridge phrase in system[1]. Added "The code diff above is what
  you must review." to both agent_loader.py and shared_analysis.py.
  Commit 271ef9e -- 875 tests still GREEN.
- REVUE-151 Task 5 (AC8-AC9): Regression validation -- 3 real diffs x 2 runs (claude-sonnet-4-6)
  Small (4f7d746, 52 lines): 5->5, delta=0. Medium (ff34daa, 144 lines): 11->11, delta=0.
  Large (d4cffcb, 472 lines): 20->20, delta=0. Verdict: PASS.
  Commit 6a0b70a -- ADR status Proposed -> Accepted. Completion Notes filled.
- Nova model decision: stays on Sonnet (developer PR replies require nuanced intent classification)

## What We Built (Session Highlights)

### Worktree policy fix
Sibling worktrees (../revue-feat-XXX) replaced by internal worktrees (.worktrees/REVUE-XXX).
The .worktrees/ directory lives inside revue.io/. Memory updated so future sessions use
`git worktree add .worktrees/REVUE-XXX-description -b feat/REVUE-XXX-description`.

### REVUE-151 D1 implementation
AnthropicClient is now a transparent passthrough -- it does not add cache_control anywhere.
Callers (LoadedAgent.analyse, run_shared_analysis) construct system as:
  system[0] = diff (with cache_control -- shared cached prefix across all agents for same PR)
  system[1] = "The code diff above is what you must review. {agent_system_prompt}"
User message = task instruction + output format (no diff content).
This gives all agents on the same PR the same cache key, enabling cross-agent cache reuse.

### Sonnet system-block analysis discovery
Direct API testing revealed: Sonnet will NOT actively analyze system-block content without
explicit cross-reference in a subsequent block. The bridge phrase "The code diff above is
what you must review." in system[1] is essential. Without it, small diffs return 0 findings.
This is a key architectural insight for any future Anthropic-specific prompt engineering.

### Regression validation methodology
Used PYTHONPATH swapping to run main vs D1 code against identical diff files without
reinstalling. Key issue found: AIConfig.from_env() pre-populates api_key from OPENAI_API_KEY
(legacy from pre-REVUE-148), causing Anthropic auth failures when both keys are in env.
Workaround: unset OPENAI_API_KEY in subprocess before running revue with Anthropic provider.
This is a pre-existing bug -- separate cleanup story recommended.

## Remaining Work - Next Steps

### 1. Push + PR for docs/REVUE-150-sprint-planning (first, quick)
SSH push was failing at session end (transient Bitbucket issue).
First action: `git push -u origin docs/REVUE-150-sprint-planning` then open Bitbucket PR.

### 2. Push + PR for feat/REVUE-151-cache-d1 (main deliverable)
Branch is in .worktrees/REVUE-151-cache-d1 with 5 commits ready.
First action: `cd .worktrees/REVUE-151-cache-d1 && git push -u origin feat/REVUE-151-cache-d1`
Then create Bitbucket PR using .bitbucket/pull_request_template.md.
Fill all template sections -- regression verdict table belongs in Description.

### 3. REVUE-113 (.revue.yml patterns) -- after REVUE-151 merges (or parallel)
Worktree .worktrees/REVUE-113-revue-yml-patterns already created (feat/REVUE-113-revue-yml-patterns).
First action: read Jira REVUE-113 via `/jira-ticket REVUE-113`, then start TDD.

### 4. AIConfig.from_env() api_key pre-population bug -- file cleanup story
Line 113 in ai_config.py: `api_key=os.getenv("OPENAI_API_KEY", "")` in from_env().
This causes Anthropic auth failures when OPENAI_API_KEY is also set (key_resolver returns
it over ANTHROPIC_API_KEY because api_key priority > api_key_env priority).
First action: create Jira ticket, assign to REVUE-150 epic.

### 5. REVUE-153 (D2 one-hour cache tier, 2pts) -- after REVUE-151 merges
Story file: _bmad-output/implementation-artifacts/revue-153-cache-d2-one-hour-tier.md
First action: verify SDK type string: `python3 -c "from anthropic.types import CacheControlEphemeralParam; print(dir(...))"`)

### 6. REVUE-154 (pipeline metrics, 8pts) -- after REVUE-151 merges
Story file: _bmad-output/implementation-artifacts/revue-154-pipeline-metrics.md
First action: create src/revue/core/metrics.py with Protocol + NullMetricsCollector + MetricsEvent
(shares ai_client.py with REVUE-151 -- coordinate merge order carefully)

### 7. REVUE-114 (.revue/comments cleanup) -- independent
To Do, no blockers. Can be picked up any time.

## Key Architectural Decisions (Session)

1. **Worktrees inside repo** -- .worktrees/ directory inside revue.io/, not sibling directories.
   Keeps /Volumes/LexarSSD/Projects/revue.io as single source of truth. Memory updated.

2. **Caller owns cache_control** -- After D1, AnthropicClient.complete() is a transparent
   passthrough. It never adds cache_control. Callers are responsible for placement.
   This makes the caching strategy explicit and testable without mocking the client.

3. **Bridge phrase in system[1] is required** -- "The code diff above is what you must review."
   Anthropic Sonnet treats system-block content as background context, not as work to do.
   Without the bridge, models applied review criteria but found nothing to analyze.
   This applies to any prompt that puts content-to-analyze in a system block.

4. **Nova stays on Sonnet** -- Developer PR replies are often terse, ambiguous, or lack proper
   context. The won't-fix reply classifier has 7 categories with nuanced distinctions
   (acknowledged_deferred vs reason_missing vs allowed_pattern). Haiku risks misclassifying
   edge cases that have real UX consequences. Sonnet stays.

5. **Rename diffs are not suitable regression test diffs** -- A rename-only commit (7354388)
   produced 0 D1 findings even after all fixes because Sonnet correctly judged no reviewable
   issues exist. Use logic-change commits (4f7d746 used here) for regression baselines.

## Session Stats

- Duration: ~6h
- Stories: 1 implemented (REVUE-151, all tasks + AC8-AC9)
- Commits: 6 (1 docs branch + 5 REVUE-151 feature commits)
- Tests: 875 passing (869 original + 6 new)
- PRs opened: 0 (SSH blocked at session end)
- Key discovery: Sonnet system-block bridge phrase requirement

## Continuation Prompt (Next Session)

Read docs/HANDOFF.md first. REVUE-151 is fully implemented in .worktrees/REVUE-151-cache-d1
(5 commits, 875 tests, regression PASS). First action: push both pending branches to Bitbucket
and open PRs. Start with docs branch: `git push -u origin docs/REVUE-150-sprint-planning`,
then feat/REVUE-151-cache-d1. After REVUE-151 merges: start REVUE-113 (worktree already created
at .worktrees/REVUE-113-revue-yml-patterns) and REVUE-154 in parallel (REVUE-153 is 2pts, pick
it up last). Also file a cleanup story for AIConfig.from_env() OPENAI_API_KEY pre-population bug.

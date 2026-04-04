# Session Handoff - 2026-04-03
**Duration:** 18:12 - 19:30 GMT (~1h 18min) | **Agent:** BMad Master

---

## Session Summary

Fixed and merged REVUE-95 (orchestrator agent selection transparency). Started with a
production regression (Anthropic returning wrong schema/empty responses) and ended with
a fully tested, SRP-compliant implementation with local CI simulation guide. PR #36 merged
with 630 tests passing. Created REVUE-107 (model-aware diff limits) and REVUE-108 (SRP
extraction) as follow-up stories.

---

## Project Status

| Metric | Value |
|--------|-------|
| Tests passing | 630 (up from 596) |
| Open PRs | None (PR #36 merged) |
| Epic REVUE-87 | 17/18 stories Done (only REVUE-105 remains) |
| Jira board | https://urukia.atlassian.net/jira/software/projects/REVUE/boards/101 |

---

## Completed This Session

- **REVUE-95 root cause diagnosis** - Claude returning wrong schema (classification/risk_level
  instead of detected_areas/selected_agents) due to conditional prompt language
- **REVUE-95 implemented** - `ecb06bb` - changed `SHARED_ANALYSIS_PROMPT` from "When
  announcing..." to "You MUST respond using ONLY this exact JSON schema"
- **Regression fixes** - `5655bb3`, `ff9bb44`, `fe50e60` - fence-stripping regex (optional
  newline), split exception handling (JSONDecodeError/ValueError -> warning, OSError/
  AttributeError -> error with exc_info=True), security warnings in docs
- **Local CI simulation guide** - `3688a05` - `docs/local-ci-simulation.md` - full guide
  for running Revue locally before CI push (lesson: 6+ CI iterations vs 1 local run)
- **Unit tests** - `5655bb3`, `ff9bb44` - 8 new tests covering Anthropic response patterns
  (wrong schema, empty fences, valid schema, mandatory prompt language)
- **CLI output fix** - `59fd79e` - removed extra blank lines in per-file review output
  (explicit `\n` in f-string + `print()` newline)
- **REVUE-108 created** - Extract format_selection_message() to revue.core.formatting (SRP)
- **REVUE-108 implemented** - `096dbb7` - SRP restored, merge-blocking issue resolved
- **REVUE-107 created** - Model-aware diff limits (10K lines is 6% of Claude Sonnet 4.5's
  200K context - unnecessarily conservative)
- **TODO comments -> Jira** - `e34a892` - replaced all TODOs with REVUE-107/REVUE-108 refs
- **PR #36 merged** - REVUE-95 -> Done in Jira

---

## What We Built (Session Highlights)

### REVUE-95 - Orchestrator Agent Selection Transparency

**Core fix:** `src/revue/core/shared_analysis.py`
- Changed `SHARED_ANALYSIS_PROMPT` to use mandatory language: "You MUST respond using
  ONLY this exact JSON schema" (no other format accepted)
- Provider-aware JSON handling: OpenAI/Google/Groq/Azure omit suffix, Anthropic appends
  explicit JSON instruction + no-fence prohibition
- Regex fence-stripping with optional newline: `r"^```(?:json)?\s*\n?"`, `r"\n?```\s*$"`
- Empty response guard after fence-stripping (prevents `JSONDecodeError` on empty string)
- Split exception handling: parsing errors (JSONDecodeError, ValueError, KeyError, TypeError)
  log at `warning`, system errors (OSError, AttributeError) log at `error` with `exc_info=True`
- `SharedAnalysisResult.orchestrator_response` field (optional, repr=False) for new format

**New module:** `src/revue/core/formatting.py`
- Extracted `format_selection_message()` from `shared_analysis.py` to restore SRP
- Handles presentation concerns: emoji-friendly output, detected areas, selected agents

**Integration:** `src/revue/core/pipeline.py`
- Passes `provider` to `run_shared_analysis()`
- Logs transparency message with guard for empty `detected_areas`/`selected_agents`
- Imports `format_selection_message` from `revue.core.formatting`

**Tests added:**
- 8 new tests in `test_shared_analysis.py`: wrong schema fallback, empty fenced response,
  plain empty string, valid new schema, valid schema in fences, mandatory prompt language,
  wrong schema with summary field graceful fallback, valid mandatory schema returns
  orchestrator_response

**Documentation:**
- `docs/local-ci-simulation.md` - step-by-step guide for running Revue locally
- Prerequisites, diff generation, PR description fetch, CLI invocation, debugging tips
- Security warnings: API key placeholder, never commit `~/.zshenv`, shell history exposure

### Key Implementation Details

**Prompt engineering fix (root cause):**
Old: "When announcing your agent selection, structure your JSON response as:"
New: "You MUST respond using ONLY this exact JSON schema - no other format is accepted:"

This fixed Claude defaulting to its own schema. The old conditional language gave Claude
choice; the new mandatory language forces compliance.

**Fence-stripping edge case:**
Claude sometimes returns `"```json\n```"` with no content between fences. Made `\n`
optional in both regex patterns to handle this edge case without crashing.

**Exception handling philosophy:**
Parsing errors (JSONDecodeError, ValueError, KeyError, TypeError) are expected in the
fallback path and log at `warning`. System errors (OSError, AttributeError) indicate
programming errors or permission issues and log at `error` with full stack trace.

---

## Remaining Work - Next Steps

1. **REVUE-105** (To Do, 3pts) - CI UX improvements (HumanizedLogger + emoji vocabulary)
   - Dependencies: REVUE-95 Done (orchestrator transparency merged)
   - First action: read REVUE-105 Jira ticket, spawn John to draft story file with DoR
   - Context: Final story in Epic REVUE-87, builds on orchestrator transparency
   - AC summary: structured logging with emojis, human-readable progress, CI-friendly output

2. **REVUE-107** (To Do) - Model-aware diff limits
   - Context: Current 10,000 line limit is 6% of Claude Sonnet 4.5's 200K context
   - Suggestion: 50K lines for Claude/GPT-4o, 10K for unknown models
   - First action: spawn John to draft story file

3. **REVUE-108** (Done) - format_selection_message extraction
   - Completed in this session (`096dbb7`)

4. **REVUE-106** (Done) - AIReviewer package absorption
   - Completed in previous session
   - Single canonical package: `src/revue/` only (no more `src/AIReviewer/`)

---

## Key Architectural Decisions (Session)

1. **Mandatory schema language in SHARED_ANALYSIS_PROMPT** - Changed from conditional
   ("When announcing your agent selection, structure...") to mandatory ("You MUST respond
   using ONLY this exact JSON schema") because Claude was defaulting to its own schema
   (classification, risk_level, review_priority) instead of the required schema
   (detected_areas, selected_agents, languages, risk_areas, summary).

2. **Optional newline in fence-stripping regex** - Made `\n` optional in both fence-strip
   patterns to handle edge case where Claude returns `"```json\n```"` with no content
   between fences.

3. **Local simulation as debugging tool** - Discovered `git diff FETCH_HEAD...HEAD` +
   CLI run catches issues faster than CI iterations. Documented in
   `docs/local-ci-simulation.md` for team.

4. **SRP extraction merge-blocking** - User decision: `format_selection_message()` SRP
   violation must be fixed before merge, not deferred. Rationale: leaving tech debt now
   means refactoring later when it's harder. Eliminated immediately.

5. **Exception handling split** - Parsing errors (expected fallback path) log at `warning`,
   system errors (programming bugs or permission issues) log at `error` with `exc_info=True`
   for security review.


---

## Lessons Learned

- **Features must be config-enabled AND documented.** Default-off features can slip through DoD if config isn't updated. REVUE-104 code was merged and marked Done, but `.revue.yml` lacked `preserve_comment_threads: true` — so the feature was dead in production. Config enablement must be an explicit AC or DoD gate.

---

## Critical Notes for Next Session

**REVUE-95 merged successfully** - All orchestrator transparency work is now in `main`.
The 630 tests include full coverage of Anthropic edge cases (wrong schema, empty fences,
valid schema in/out of fences).

**REVUE-106 completed** - Single canonical package: `src/revue/` only. The dual codebase
issue from previous sessions is resolved. `src/AIReviewer/` no longer exists.

**SDLC discipline reminder** - Spawn real agents for every role. This session had one
near-violation (Amelia spawned for unit tests after user requested them explicitly) but
was caught and corrected. The pattern: CI/test reveals bug -> urgency -> spawn Amelia
(never implement directly).

**Test command (single suite):**
```bash
cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q
```

**Local CI simulation:**
```bash
source ~/.zshenv && cd Projects/revue.io
git fetch "https://x-token-auth:${BITBUCKET_API_TOKEN}@bitbucket.org/cbscd/revue.git" main
git diff FETCH_HEAD...HEAD > /tmp/revue_pr.diff
# (see docs/local-ci-simulation.md for full steps)
```

**Environment variables (required for local testing):**
- `AI_API_KEY`, `AI_PROVIDER`, `AI_MODEL`
- `REVUE_TIER_OVERRIDE=pro` (staging only)
- `BITBUCKET_USERNAME`, `BITBUCKET_API_TOKEN`
- `APP_ENV=staging`

---

## Session Stats

- Duration: ~1h 18min
- Stories completed: REVUE-95 (root cause diagnosis + implementation + tests + SRP fix)
- Follow-up stories created: REVUE-107, REVUE-108
- PRs merged: #36 (REVUE-95)
- Commits: 10 (ecb06bb to 096dbb7)
- Tests: 630 passing (up from 596)
- New files: formatting.py, local-ci-simulation.md
- Party mode agents used: Amelia (implementation + tests + fixes), Bob (DoD gates)

---

## Continuation Prompt (Next Session)

```
Read docs/HANDOFF.md for full context.

Epic REVUE-87 is 17/18 Done. One story remains:
- REVUE-105 - CI UX improvements (HumanizedLogger + emoji vocabulary) - 3pts

Start with REVUE-105: read the Jira ticket, spawn John to draft the story file with
full DoR context. This builds on REVUE-95 (orchestrator transparency) which is now
merged.

SDLC: spawn real agents for every role. BMad Master orchestrates only - never writes
code, runs tests, or fixes bugs directly.

Test command: cd src && PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q
Local CI guide: docs/local-ci-simulation.md
```

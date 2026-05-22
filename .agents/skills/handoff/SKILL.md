---
name: "handoff"
description: "Produce a clean session handoff document. Use when the user says \"handoff\", \"wrap up the session\", or asks to create a handoff document. Finds all HANDOFF.md locations, writes identical content to each, commits, and copies the continuation prompt to clipboard."
---

Produce a clean handoff document so the next session can continue without re-discovering context.

**All HANDOFF.md files must stay in sync.**
The workspace may contain multiple `HANDOFF.md` files -- one at the workspace root
(`docs/HANDOFF.md`) and one inside the project (`Projects/<name>/docs/HANDOFF.md`).
Both must be written with identical content on every handoff. Use `find` to discover
all locations before writing. Do not leave any copy stale.
Do not create or maintain `session-continuation.md`, `SESSION-CONTINUATION.md`, or any
other handoff-style duplicate. If those files exist, note them as legacy.

## Step 1 -- Gather session context

```bash
git log --oneline -20
git status --short
```

Also read (if they exist):
- `docs/HANDOFF.md` -- previous handoff (read this for context, then overwrite)
- `docs/sprint-plan.md` -- sprint and story status
- `docs/prd.md` -- PRD version and open items

## Step 2 -- Summarise what was completed this session

Compare previous `HANDOFF.md` (pending items) against the git log. Write a bullet list
of what was completed -- include story IDs and commit hashes where relevant.

## Step 3 -- List remaining work with specific next steps

Identify what is in-progress or blocked, and the **concrete first action** for the next
session (name the file, function, or command -- not vague). Order by priority.

## Step 4 -- Identify all HANDOFF.md locations and write them

Before writing, find every existing `HANDOFF.md` in the workspace:

```bash
find . -name "HANDOFF.md" 2>/dev/null
```

Write the same content to ALL locations found. This ensures the project-level handoff
(e.g. `Projects/revue.io/docs/HANDOFF.md`) and the workspace-level handoff
(e.g. `docs/HANDOFF.md`) stay in sync. If no project-level file exists yet, only write
to `docs/HANDOFF.md`. Never leave one copy stale while updating the other.

Create or overwrite every `HANDOFF.md` found with this structure:

```markdown
# Session Handoff -- <date>
**Duration:** <start> - <end> GMT | **Agent:** BMad Master

## Session Summary
<2-3 sentence overview of what was accomplished>

## Project Status
| Metric | Value |
|--------|-------|
| Stories complete | X/Y |
| Tests passing | N |
| Open PRs | ... |

## Completed this session
<bullet list with story IDs and commit hashes>

## What We Built (Session Highlights)
<one subsection per story/feature with key implementation details>

## Remaining Work -- Next Steps
<ordered list, each with a concrete first action>

## Key Architectural Decisions (Session)
<numbered list of decisions made, with rationale -- omit if none>

## Session Stats
- Duration: Xh Ymin
- Stories: N completed
- Commits: N
- Tests: N passing
- PRs opened: #N, #N
- Party mode agents used: John, Winston, Amelia, Mary, Bob

## Continuation Prompt (Next Session)
<see Step 5>
```

**CRITICAL: Use only ASCII-safe characters:**
- Use `-` (hyphen) instead of `--` (em dash)
- Use `->` instead of right arrow
- Use regular quotes `"` instead of smart quotes
- Use `...` instead of ellipsis character

This prevents encoding issues when copied to clipboard or viewed in different terminals.

## Step 5 -- Generate a continuation prompt

Under `## Continuation Prompt (Next Session)`, write a ready-to-paste prompt. Include:
- What was just completed
- Next story to implement (ID + name)
- Any blockers or open decisions
- Reminder to read `docs/HANDOFF.md`

Keep it under 10 lines -- dense and actionable.

## Step 6 -- Copy continuation prompt to clipboard

Use the workspace-root `docs/HANDOFF.md` as the source (all copies are identical):

```bash
awk '/^## Continuation Prompt/{found=1; next} found && /^## /{exit} found{print}' docs/HANDOFF.md | pbcopy
```

Confirm to the user that it's copied and ready to paste.

## Step 7 -- Commit

Stage ALL HANDOFF.md files that were written:

```bash
git add $(find . -name "HANDOFF.md" | sed 's|^\./||')
git commit -m "docs: session handoff <date>"
```

Create `docs/` first if it doesn't exist. Do not stage other files unless explicitly asked.

## Step 8 -- Note legacy files (if present)

If `docs/session-continuation.md` or similar files exist, inform the user they are legacy
and suggest removing them in a follow-up commit. Do not delete them automatically.

## MANUAL MIGRATION REQUIRED

Claude `allowed-tools` was preserved as prompt guidance, not a Codex permission boundary.

You're allowed to use these tools:

- Read Write Edit Bash Glob

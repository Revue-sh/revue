---
name: dogfood
model: haiku
description: Run a local Revue AI code review against the current branch. Use when the user says "dogfood", "run the simulation", "run the review locally", or "check this before opening a PR". Diffs against origin/main (or a specified base branch), runs the full AI review, and prints findings here — nothing is posted anywhere. No PR required.
allowed-tools: Bash
---

Run the Revue AI review locally. Findings print here so you can fix them before opening a PR.

## Script

```
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/dogfood"
```

```bash
# Diff vs origin/main (default)
"$SKILL_DIR/scripts/dogfood.sh"

# Diff vs a different base branch
"$SKILL_DIR/scripts/dogfood.sh" develop
```

## Parsing the user's argument

| User says | Call |
|-----------|------|
| `/dogfood` | `dogfood.sh` (base = main) |
| `/dogfood develop` | `dogfood.sh develop` |

## Prerequisites

Only `REVUE_ANTHROPIC_API_KEY` is required (sourced from `~/.zshenv`). No Bitbucket credentials needed.

## Output format

After the script completes, present:

1. **Verdict line** — the `[revue]` verdict line (e.g. `✅ Review cycle complete`)
2. **Finding summary** — count by severity and list of medium+ findings with file, line, and issue
3. **Next step** — offer to fix any findings here before the PR is opened

---
name: dogfood
model: haiku
description: Simulate the Bitbucket pipeline Revue review step locally against an open PR. Use when the user says "dogfood", "run the simulation", "simulate the pipeline", or "run the review against PR #N". Mirrors the revue-review step in bitbucket-pipelines.yml exactly — generates the diff, fetches the PR description, validates config, then runs the full AI review and posts inline comments to the PR.
allowed-tools: Bash
---

Simulate the `revue-review` CI step locally against a Bitbucket PR.

## Script

```
SKILL_DIR="/Volumes/LexarSSD/Projects/revue.io/.claude/skills/dogfood"
```

Single entrypoint:

```bash
# Auto-detect PR from current branch
"$SKILL_DIR/scripts/dogfood.sh"

# Explicit PR number
"$SKILL_DIR/scripts/dogfood.sh" 58
```

The script:
1. Resolves the PR number (argument or auto-detect from current branch via Bitbucket API)
2. Fetches the PR's destination branch and description
3. Generates `git diff origin/<destination>...HEAD`
4. Validates `.revue.yml`
5. Runs `revue review` with `--platform bitbucket`, posting inline comments to the PR

## Parsing the user's argument

| User says | Call |
|-----------|------|
| `/dogfood` | `dogfood.sh` (auto-detect) |
| `/dogfood 58` | `dogfood.sh 58` |
| `/dogfood PR #58` | `dogfood.sh 58` |

Strip any `#` or `PR` prefix — pass the bare number.

## Prerequisites

All credentials are read from `~/.zshenv` by the script — always `source ~/.zshenv` is handled internally. Required env vars:

| Var | Purpose |
|-----|---------|
| `ANTHROPIC_API_KEY` | AI review calls |
| `BITBUCKET_USERNAME` | Bitbucket API auth |
| `BITBUCKET_API_TOKEN` | Bitbucket API auth |
| `BITBUCKET_WORKSPACE` | Repo workspace (cbscd) |

If `ANTHROPIC_API_KEY` is missing the script will fail with a clear error from the CLI — tell the user to set it in `~/.zshenv`.

## Output format

After the script completes, present:

1. **Verdict line** — pass the `[revue]` verdict line from output (e.g. `✅ Review cycle complete` or `❌ All agents failed`)
2. **Finding summary** — count by severity (high/medium/low/info) and list the medium+ findings with file, line, and issue
3. **Next step** — suggest whether to address any findings before merging, or confirm the PR is clean

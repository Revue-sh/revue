---
name: "bitbucket-delete-comments"
description: "Delete comments from a Bitbucket PR. Supports deleting blank/deleted comments, Revue finding roots with no replies, or specific comment IDs. Use when nearing the 200-comment Bitbucket limit or cleaning up stale bot output. Invoked as /bitbucket-delete-comments [workspace/repo] [PR#] [options]."
---

Delete comments from a Bitbucket PR. Default repo: `cbscd/revue`.

## Arguments

| Argument | Default | Description |
|---|---|---|
| `workspace/repo` | `cbscd/revue` | Target repository |
| `PR#` | required | Pull request number |
| `--mode blank` | — | Delete blank and already-deleted comments |
| `--mode no-replies` | — | Delete Revue finding roots with no developer replies |
| `--mode all-safe` | **default** | Both of the above |
| `--ids 123,456` | — | Delete specific comment IDs unconditionally |
| `--dry-run` | — | Preview without deleting |

## Instructions

Parse the user's arguments, then run:

```bash
source ~/.zshenv && python3 "${CLAUDE_SKILL_DIR}/scripts/delete_comments.py" $ARGUMENTS
```

If only a PR number is given (no repo), prepend `cbscd/revue`:

```bash
source ~/.zshenv && python3 "${CLAUDE_SKILL_DIR}/scripts/delete_comments.py" cbscd/revue <PR#>
```

After the script runs, report the deleted count and estimated remaining count to the user.
If the remaining count is still above 190, warn and suggest running again.

## Script reference

See [`scripts/delete_comments.py`](scripts/delete_comments.py) for implementation details.

## MANUAL MIGRATION REQUIRED

Claude `allowed-tools` was preserved as prompt guidance, not a Codex permission boundary.

You're allowed to use these tools:

- Bash

Review unsupported Claude skill fields manually: `argument-hint`, `disable-model-invocation`, `model`.

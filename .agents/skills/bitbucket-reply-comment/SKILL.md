---
name: "bitbucket-reply-comment"
description: "Post a reply to a Bitbucket PR comment thread. Use when the user wants to respond to a specific PR comment, reply to a thread by ID, or post multiple replies at once. Invoked as /bitbucket-reply-comment [workspace/repo] [PR#] [thread-id] [message]."
---

Post a reply to a Bitbucket PR comment thread. Default repo: `cbscd/revue`.

## Arguments

| Argument | Default | Description |
|---|---|---|
| `workspace/repo` | `cbscd/revue` | Target repository |
| `PR#` | required | Pull request number |
| `parent-comment-id` | required (single mode) | ID of the root comment to reply to |
| `"message"` | — | Reply body as a quoted string |
| `--batch replies.json` | — | JSON file with multiple replies: `[{parent_id, body}, …]` |

## Instructions

Parse the user's arguments to determine the target thread and message body.

**Single reply** — the user provides a thread ID and message:

```bash
source ~/.zshenv && python3 "${CLAUDE_SKILL_DIR}/scripts/reply_comment.py" \
  cbscd/revue <PR#> <parent_id> --body "<message>"
```

If only a PR number is given (no repo), prepend `cbscd/revue`.

**Multiple replies** — when the user provides several thread IDs with messages, write them to a temp file and use batch mode:

```bash
source ~/.zshenv && python3 "${CLAUDE_SKILL_DIR}/scripts/reply_comment.py" \
  cbscd/revue <PR#> --batch /tmp/replies_<PR#>.json
```

The batch JSON format is:
```json
[
  {"parent_id": 780891903, "body": "First reply text."},
  {"parent_id": 780905432, "body": "Second reply text."}
]
```

IMPORTANT: Before posting any reply, run the `/humaniser` skill on the reply body to remove AI writing patterns. Draft the reply, humanise it, then post the humanised version.

After posting, report each new comment ID and confirm success. If a 403 is returned, the PR may be at the 200-comment limit — suggest running `/bitbucket-delete-comments` first.

## Script reference

See [`scripts/reply_comment.py`](scripts/reply_comment.py) for implementation details.

## MANUAL MIGRATION REQUIRED

Claude `allowed-tools` was preserved as prompt guidance, not a Codex permission boundary.

You're allowed to use these tools:

- Bash

Review unsupported Claude skill fields manually: `argument-hint`, `disable-model-invocation`, `model`.

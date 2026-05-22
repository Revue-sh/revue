---
name: "pr-comments"
description: "Read and display PR/MR comments and reply threads from Bitbucket, GitHub, or GitLab. Use when the user asks to see PR comments, check review feedback, read reply threads, or analyse comment dispositions (won't fix, false positive, fixed). Invoked as /pr-comments [platform] [pr-id] or /pr-comments bitbucket 42."
---

Fetch and display PR comments with reply threads from Bitbucket, GitHub, or GitLab.

## Configuration (source ~/.zshenv first)

| Platform | Token env var | Repo |
|----------|--------------|------|
| Bitbucket | `BITBUCKET_API_TOKEN` + `BITBUCKET_USERNAME` | `cbscd/revue` |
| GitHub | `GITHUB_TOKEN` | `cbscd/revue-test-github` |
| GitLab | `GITLAB_TOKEN` | `urukia-group/revue-test-gitlab` |

> **Bitbucket auth note:** Always use basic auth: `-u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}"` for ALL calls (GET and write). Bearer token fails on Bitbucket Cloud even for read operations.

## Instructions

Parse the user's argument for platform and PR/MR number. If only a number is given, default to Bitbucket. If no argument, default to the most recent open PR on Bitbucket.

Always `source ~/.zshenv` before any curl call.

---

### Bitbucket

Use the pre-approved scripts to avoid repeated Bash approval prompts:

**Fetch a single comment:**
```bash
/Volumes/LexarSSD/Projects/revue.io/.claude/skills/pr-comments/scripts/bb_get_comment.sh <comment_id> [pr_number] [repo]
# Default pr_number: 78, repo: cbscd/revue
# Example: bb_get_comment.sh 786361663 78
```

**Fetch all comments on a PR:**
```bash
/Volumes/LexarSSD/Projects/revue.io/.claude/skills/pr-comments/scripts/bb_get_comment.sh all <pr_number>
# Example: bb_get_comment.sh all 78
```

**Post a reply to a Bitbucket comment:**
```bash
/Volumes/LexarSSD/Projects/revue.io/.claude/skills/pr-comments/scripts/bb_post_reply.sh <parent_comment_id> "<body>" [pr_number] [repo]
# Defaults: PR #48, repo cbscd/revue
# Example: bb_post_reply.sh 781997361 "Won't fix — tracked as REVUE-138." 48
```

---

### GitHub

Use these pre-approved scripts to avoid repeated Bash approval prompts:

**Fetch a single comment:**
```bash
/Volumes/LexarSSD/Projects/revue.io/.claude/skills/pr-comments/scripts/gh_get_comment.sh <comment_id> [repo]
# Default repo: cbscd/revue-test-github
```

**Post a reply to a comment thread:**
```bash
/Volumes/LexarSSD/Projects/revue.io/.claude/skills/pr-comments/scripts/gh_post_reply.sh <parent_comment_id> "<body>" [pr_number] [repo]
# Defaults: PR #4, repo cbscd/revue-test-github
# Example: gh_post_reply.sh 3066014233 "False positive — already handled.\n\n[//]: # (revue:ack)"
```

To list **all comments on a PR**:

```bash
source ~/.zshenv && gh api repos/cbscd/revue-test-github/pulls/4/comments --paginate | python3 -c "
import json, sys
from collections import defaultdict
comments = json.load(sys.stdin)
roots = [c for c in comments if not c.get('in_reply_to_id')]
replies_map = defaultdict(list)
for c in comments:
    if c.get('in_reply_to_id'):
        replies_map[c['in_reply_to_id']].append(c)
print(f'Total: {len(comments)} ({len(roots)} root, {len(comments)-len(roots)} replies)')
print()
for i, root in enumerate(sorted(roots, key=lambda x: x['id']), 1):
    body = (root.get('body') or '')[:160]
    path = root.get('path', '')
    line = root.get('line') or root.get('original_line', '')
    user = root.get('user', {}).get('login', '?')
    reps = replies_map.get(root['id'], [])
    print(f'[{i}] #{root[\"id\"]} by {user} — {path}:{line}')
    print(f'     {body}')
    for r in sorted(reps, key=lambda x: x['id']):
        ru = r.get('user', {}).get('login', '?')
        rb = (r.get('body') or '')[:110]
        print(f'     → #{r[\"id\"]} by {ru}: {rb}')
    print()
"
```

For PR-level comments (not inline):

```bash
source ~/.zshenv && gh api repos/cbscd/revue-test-github/issues/4/comments --paginate | python3 -c "
import json, sys
comments = json.load(sys.stdin)
print(f'PR issue comments: {len(comments)}')
for c in comments:
    user = c.get('user', {}).get('login', '?')
    body = (c.get('body') or '')[:200]
    print(f'  #{c[\"id\"]} by {user}: {body[:160]}')
"
```

---

### GitLab

```bash
source ~/.zshenv && curl -s \
  -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "https://gitlab.com/api/v4/projects/urukia-group%2Frevue-test-gitlab/merge_requests/3/notes?per_page=100" | python3 -c "
import json, sys
notes = json.load(sys.stdin)
print(f'MR notes: {len(notes)}')
for n in notes:
    user = n.get('author', {}).get('name', '?')
    body = (n.get('body') or '')[:200]
    resolved = ' [RESOLVED]' if n.get('resolved') else ''
    print(f'  #{n[\"id\"]} by {user}{resolved}: {body[:160]}')
"
```

---

## Output format

After fetching, present a clean summary:
1. **Count**: total comments, top-level vs replies
2. **Thread view**: each root comment with its replies indented beneath it
3. **Disposition summary**: count of Won't Fix, False Positive, Fixed, Unresolved
4. **Unresolved top-level** findings (no reply yet): list them for the user's attention

If the user asks to check reply dispositions specifically, highlight which comments have won't-fix/false-positive replies and which are unresolved — this feeds into the REVUE-112 reply tracking work.

## MANUAL MIGRATION REQUIRED

Claude `allowed-tools` was preserved as prompt guidance, not a Codex permission boundary.

You're allowed to use these tools:

- Bash

Review unsupported Claude skill fields manually: `model`.

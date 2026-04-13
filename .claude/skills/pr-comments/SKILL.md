---
name: pr-comments
model: haiku
description: Read and display PR/MR comments and reply threads from Bitbucket, GitHub, or GitLab. Use when the user asks to see PR comments, check review feedback, read reply threads, or analyse comment dispositions (won't fix, false positive, fixed). Invoked as /pr-comments [platform] [pr-id] or /pr-comments bitbucket 42.
allowed-tools: Bash
---

Fetch and display PR comments with reply threads from Bitbucket, GitHub, or GitLab.

## Configuration (source ~/.zshenv first)

| Platform | Token env var | Repo |
|----------|--------------|------|
| Bitbucket | `BITBUCKET_API_TOKEN` | `cbscd/revue` |
| GitHub | `GITHUB_TOKEN` | `cbscd/revue-test-github` |
| GitLab | `GITLAB_TOKEN` | `urukia-group/revue-test-gitlab` |

## Instructions

Parse the user's argument for platform and PR/MR number. If only a number is given, default to Bitbucket. If no argument, default to the most recent open PR on Bitbucket.

Always `source ~/.zshenv` before any curl call.

---

### Bitbucket

```bash
source ~/.zshenv && curl -s \
  -H "Authorization: Bearer ${BITBUCKET_API_TOKEN}" \
  "https://api.bitbucket.org/2.0/repositories/cbscd/revue/pullrequests/42/comments?pagelen=100" | python3 -c "
import json, sys

data = json.load(sys.stdin)
comments = data.get('values', [])

# Build thread map: id -> comment
by_id = {c['id']: c for c in comments}

# Top-level comments (no parent)
roots = [c for c in comments if 'parent' not in c]
replies = [c for c in comments if 'parent' in c]

# Group replies by parent id
from collections import defaultdict
children = defaultdict(list)
for r in replies:
    children[r['parent']['id']].append(r)

def disposition(text):
    t = text.lower()
    if 'won\\'t fix' in t or 'wont fix' in t: return 'WON\\'T FIX'
    if 'false positive' in t: return 'FALSE POSITIVE'
    if 'fixed' in t or 'fix:' in t: return 'FIXED'
    return ''

def fmt(c, indent=''):
    user = c.get('user', {}).get('display_name', '?')
    resolved = ' [RESOLVED]' if c.get('resolved') else ''
    content = (c.get('content', {}).get('raw') or '')[:200]
    disp = disposition(content)
    disp_tag = f' [{disp}]' if disp else ''
    print(f'{indent}#{c[\"id\"]} by {user}{resolved}{disp_tag}')
    print(f'{indent}  {content[:160]}')

print(f'Total comments: {len(comments)} ({len(roots)} top-level, {len(replies)} replies)')
print()
for root in sorted(roots, key=lambda x: x['id']):
    fmt(root)
    for reply in sorted(children.get(root['id'], []), key=lambda x: x['id']):
        fmt(reply, indent='  → ')
    print()
"
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

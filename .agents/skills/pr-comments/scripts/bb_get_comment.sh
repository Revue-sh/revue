#!/usr/bin/env bash
# Fetch a single Bitbucket PR comment (or all comments on a PR).
#
# Usage:
#   bb_get_comment.sh <comment_id> [pr_number] [repo]
#   bb_get_comment.sh all [pr_number] [repo]
#
# Defaults:
#   pr_number : 78
#   repo      : cbscd/revue
#
# Auth: basic auth (BITBUCKET_USERNAME:BITBUCKET_API_TOKEN)
# Note: Bearer header fails for Bitbucket Cloud even on GET requests.

set -euo pipefail

COMMENT_ID="${1:?Usage: bb_get_comment.sh <comment_id|all> [pr_number] [repo]}"
PR_NUMBER="${2:-78}"
REPO="${3:-cbscd/revue}"

source ~/.zshenv

BASE_URL="https://api.bitbucket.org/2.0/repositories/${REPO}/pullrequests/${PR_NUMBER}/comments"

if [ "$COMMENT_ID" = "all" ]; then
    URL="${BASE_URL}?pagelen=100"
else
    URL="${BASE_URL}/${COMMENT_ID}"
fi

HTTP_CODE="$(curl -s -o /tmp/bb_comment_response.json -w "%{http_code}" \
  -u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" \
  "$URL")"

if [ "$HTTP_CODE" != "200" ]; then
    echo "ERROR: HTTP ${HTTP_CODE}"
    cat /tmp/bb_comment_response.json
    exit 1
fi

if [ "$COMMENT_ID" = "all" ]; then
    python3 << 'PYEOF'
import json, sys
from collections import defaultdict

with open('/tmp/bb_comment_response.json') as f:
    data = json.load(f)

comments = data.get('values', [])
roots = [c for c in comments if 'parent' not in c]
replies = [c for c in comments if 'parent' in c]

children = defaultdict(list)
for r in replies:
    children[r['parent']['id']].append(r)

def disposition(text):
    t = text.lower()
    if "won't fix" in t or 'wont fix' in t: return "WON'T FIX"
    if 'false positive' in t: return 'FALSE POSITIVE'
    if 'fixed' in t or 'fix:' in t: return 'FIXED'
    return ''

def fmt(c, indent=''):
    user = c.get('user', {}).get('display_name', '?')
    resolved = ' [RESOLVED]' if c.get('resolved') else ''
    content = (c.get('content', {}).get('raw') or '')
    disp = disposition(content)
    disp_tag = f' [{disp}]' if disp else ''
    print(f'{indent}#{c["id"]} by {user}{resolved}{disp_tag}')
    print(f'{indent}  {content[:200]}')

print(f'Total comments: {len(comments)} ({len(roots)} top-level, {len(replies)} replies)')
print()
for root in sorted(roots, key=lambda x: x['id']):
    fmt(root)
    for reply in sorted(children.get(root['id'], []), key=lambda x: x['id']):
        fmt(reply, indent='  → ')
    print()
PYEOF
else
    python3 << 'PYEOF'
import json

with open('/tmp/bb_comment_response.json') as f:
    c = json.load(f)

user = c.get('user', {}).get('display_name', '?')
resolved = ' [RESOLVED]' if c.get('resolved') else ''
created = c.get('created_on', '?')
content = c.get('content', {}).get('raw', '(no content)')
parent = f" (reply to #{c['parent']['id']})" if 'parent' in c else ''

print(f"Comment #{c['id']} by {user}{resolved}{parent}")
print(f"Created: {created}")
print()
print(content)
PYEOF
fi

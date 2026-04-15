#!/usr/bin/env bash
# Post a reply to a Bitbucket PR comment.
#
# Usage: bb_post_reply.sh <parent_comment_id> "<body>" [pr_number] [repo]
#
# Defaults:
#   pr_number : 48
#   repo      : cbscd/revue
#
# Auth: basic auth (BITBUCKET_USERNAME:BITBUCKET_API_TOKEN)
# Note: Bearer header returns 401 for write calls on Bitbucket Cloud.

set -euo pipefail

PARENT_ID="${1:?Usage: bb_post_reply.sh <parent_comment_id> \"<body>\" [pr_number] [repo]}"
BODY="${2:?body is required}"
PR_NUMBER="${3:-48}"
REPO="${4:-cbscd/revue}"

source ~/.zshenv

PAYLOAD="$(python3 -c "
import json, sys
body = sys.argv[1]
parent_id = int(sys.argv[2])
print(json.dumps({'content': {'raw': body}, 'parent': {'id': parent_id}}))
" "$BODY" "$PARENT_ID")"

HTTP_CODE="$(curl -s -o /tmp/bb_reply_response.json -w "%{http_code}" \
  -u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -X POST \
  "https://api.bitbucket.org/2.0/repositories/${REPO}/pullrequests/${PR_NUMBER}/comments" \
  --data-binary "$PAYLOAD")"

if [ "$HTTP_CODE" = "201" ]; then
    COMMENT_ID="$(python3 -c "import json; d=json.load(open('/tmp/bb_reply_response.json')); print(d['id'])")"
    echo "Posted reply #${COMMENT_ID} on PR #${PR_NUMBER} (parent: ${PARENT_ID})"
else
    echo "ERROR: HTTP ${HTTP_CODE}"
    cat /tmp/bb_reply_response.json
    exit 1
fi

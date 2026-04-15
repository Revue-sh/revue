#!/usr/bin/env bash
# Post a reply to a GitLab MR discussion thread.
#
# Usage: gl_post_reply.sh <discussion_id> "<body>" [mr_iid] [project_id]
#
# Defaults:
#   mr_iid     : 4
#   project_id : 80941201  (urukia-group/revue-test-gitlab)
#
# Auth: PRIVATE-TOKEN header via GITLAB_TOKEN env var (sourced from ~/.zshenv)
#
# GitLab uses discussion IDs (hex strings), not parent comment IDs.
# To find the discussion ID for a note, list discussions:
#   GET /projects/{id}/merge_requests/{iid}/discussions

set -euo pipefail

DISCUSSION_ID="${1:?Usage: gl_post_reply.sh <discussion_id> \"<body>\" [mr_iid] [project_id]}"
BODY="${2:?body is required}"
MR_IID="${3:-4}"
PROJECT_ID="${4:-80941201}"

source ~/.zshenv

PAYLOAD="$(python3 -c "import json, sys; print(json.dumps({'body': sys.argv[1]}))" "$BODY")"

HTTP_CODE="$(curl -s -o /tmp/gl_reply_response.json -w "%{http_code}" \
  --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  --header "Content-Type: application/json" \
  -X POST \
  "https://gitlab.com/api/v4/projects/${PROJECT_ID}/merge_requests/${MR_IID}/discussions/${DISCUSSION_ID}/notes" \
  --data-binary "$PAYLOAD")"

if [ "$HTTP_CODE" = "201" ]; then
    NOTE_ID="$(python3 -c "import json; d=json.load(open('/tmp/gl_reply_response.json')); print(d['id'])")"
    echo "Posted reply note #${NOTE_ID} on MR #${MR_IID} (discussion: ${DISCUSSION_ID})"
else
    echo "ERROR: HTTP ${HTTP_CODE}"
    cat /tmp/gl_reply_response.json
    exit 1
fi

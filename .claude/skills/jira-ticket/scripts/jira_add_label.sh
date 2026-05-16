#!/usr/bin/env bash
# Add a label to an existing Jira ticket (labels are an array field, not handled by jira_update.sh).
# Usage: jira_add_label.sh <KEY> <label>
#
# Uses the same auth pattern as the rest of this skill: BITBUCKET_USERNAME + JIRA_API_TOKEN.
# Uses PUT /rest/api/2/issue/{key} with the "update" verb to merge (not replace) the labels array.
set -euo pipefail
source ~/.zshenv

KEY="${1:?Usage: jira_add_label.sh KEY LABEL}"
LABEL="${2:?Usage: jira_add_label.sh KEY LABEL}"

PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'update': {'labels': [{'add': sys.argv[1]}]}}))
" "$LABEL")

HTTP_CODE=$(curl -s -o /tmp/jira-label-resp.txt -w "%{http_code}" -X PUT \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    "https://urukia.atlassian.net/rest/api/2/issue/${KEY}")

if [[ "$HTTP_CODE" == "204" ]]; then
    echo "Labeled: ${KEY} += ${LABEL}"
    echo "URL:     https://urukia.atlassian.net/browse/${KEY}"
else
    echo "ERROR HTTP ${HTTP_CODE}"
    cat /tmp/jira-label-resp.txt
    exit 1
fi

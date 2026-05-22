#!/usr/bin/env bash
# Update a single string field on an existing Jira ticket.
# Usage: jira_update.sh <KEY> <field> [<value>]
#
# value: passed via $3 or stdin (heredoc/pipe). Designed for plain-string fields
#        like description (Jira wiki markup) and summary. For array/object fields
#        (labels, components, etc.), call the API directly.
#
# Examples:
#   jira_update.sh REVUE-247 summary "New summary"
#
#   jira_update.sh REVUE-247 description <<'EOF'
#   h2. User Story
#   ...
#   EOF
#
# Uses PUT /rest/api/2/issue/{key} — v2 to stay consistent with the rest of this skill.
set -euo pipefail
source ~/.zshenv

KEY="${1:?Usage: jira_update.sh KEY FIELD [VALUE]}"
FIELD="${2:?Usage: jira_update.sh KEY FIELD [VALUE]}"

if [[ -n "${3:-}" ]]; then
    VALUE="$3"
elif [[ ! -t 0 ]]; then
    VALUE="$(cat)"
else
    echo "ERROR: no value supplied (pass as \$3 or pipe via stdin)" >&2
    exit 1
fi

PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'fields': {sys.argv[1]: sys.argv[2]}}))
" "$FIELD" "$VALUE")

HTTP_CODE=$(curl -s -o /tmp/jira-update-resp.txt -w "%{http_code}" -X PUT \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    "https://urukia.atlassian.net/rest/api/2/issue/${KEY}")

if [[ "$HTTP_CODE" == "204" ]]; then
    echo "Updated: ${KEY} (${FIELD})"
    echo "URL:     https://urukia.atlassian.net/browse/${KEY}"
else
    echo "ERROR HTTP ${HTTP_CODE}"
    cat /tmp/jira-update-resp.txt
    exit 1
fi

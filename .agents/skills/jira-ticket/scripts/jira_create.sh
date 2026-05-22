#!/usr/bin/env bash
# Create a Jira ticket in the REVUE project.
# Usage: jira_create.sh "Summary text" [epic_key] [label] [description] [issue_type_id]
#
# description: Jira wiki markup string (h2. headings, *bold*, etc.).
#              Pass via $4 or pipe/heredoc into stdin if omitted.
#
# issue_type_id: 10112=Task (default), 10113=Epic, 10114=Subtask
# Uses API v2 — v3 returns 404 on POST.
set -euo pipefail
source ~/.zshenv

SUMMARY="${1:?Usage: jira_create.sh \"Summary\" [EPIC_KEY] [label] [description] [issue_type_id]}"
EPIC_KEY="${2:-}"
LABEL="${3:-}"
ISSUE_TYPE_ID="${5:-10112}"

# Accept description from $4 or stdin
if [[ -n "${4:-}" ]]; then
    DESCRIPTION="$4"
elif [[ ! -t 0 ]]; then
    DESCRIPTION="$(cat)"
else
    DESCRIPTION=""
fi

# Build fields JSON
FIELDS=$(python3 -c "
import json, sys
fields = {
    'project': {'key': 'REVUE'},
    'issuetype': {'id': sys.argv[5]},
    'summary': sys.argv[1],
}
if sys.argv[2]:
    fields['parent'] = {'key': sys.argv[2]}  # Epic (next-gen project uses parent)
if sys.argv[3]:
    fields['labels'] = [sys.argv[3]]
if sys.argv[4]:
    fields['description'] = sys.argv[4]
print(json.dumps({'fields': fields}))
" "$SUMMARY" "$EPIC_KEY" "$LABEL" "$DESCRIPTION" "$ISSUE_TYPE_ID")

curl -s -X POST \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$FIELDS" \
    "https://urukia.atlassian.net/rest/api/2/issue" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'key' in d:
    print(f'Created: {d[\"key\"]}')
    print(f'URL:     https://urukia.atlassian.net/browse/{d[\"key\"]}')
else:
    print('ERROR:', json.dumps(d, indent=2))
    sys.exit(1)
"

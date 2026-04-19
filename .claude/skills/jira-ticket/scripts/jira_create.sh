#!/usr/bin/env bash
# Create a Jira ticket in the REVUE project.
# Usage: jira_create.sh "Summary text" [epic_key] [label] [description]
#
# description: Jira wiki markup string (h2. headings, *bold*, etc.).
#              Pass via $4 or pipe/heredoc into stdin if omitted.
#
# Issue type: Task (10112). For epics use the Jira UI.
# Uses API v2 — v3 returns 404 on POST.
set -euo pipefail
source ~/.zshenv

SUMMARY="${1:?Usage: jira_create.sh \"Summary\" [EPIC_KEY] [label] [description]}"
EPIC_KEY="${2:-}"
LABEL="${3:-}"

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
    'issuetype': {'id': '10112'},
    'summary': sys.argv[1],
}
if sys.argv[2]:
    fields['parent'] = {'key': sys.argv[2]}  # Epic (next-gen project uses parent)
if sys.argv[3]:
    fields['labels'] = [sys.argv[3]]
if sys.argv[4]:
    fields['description'] = sys.argv[4]
print(json.dumps({'fields': fields}))
" "$SUMMARY" "$EPIC_KEY" "$LABEL" "$DESCRIPTION")

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

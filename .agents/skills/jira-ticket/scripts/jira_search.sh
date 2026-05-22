#!/usr/bin/env bash
# Search Jira tickets by JQL.
# Usage: jira_search.sh "project=REVUE AND status='To Do'" [maxResults]
set -euo pipefail
source ~/.zshenv

JQL="${1:-project=REVUE ORDER BY updated DESC}"
MAX="${2:-20}"

curl -s \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    -X POST "https://urukia.atlassian.net/rest/api/3/search/jql" \
    -H "Content-Type: application/json" \
    -d "{\"jql\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$JQL"),\"fields\":[\"key\",\"summary\",\"status\",\"issuetype\",\"priority\",\"parent\"],\"maxResults\":${MAX}}" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
issues = d.get('issues', [])
for i in issues:
    f = i['fields']
    status   = f.get('status', {}).get('name', '?')
    itype    = f.get('issuetype', {}).get('name', '?')
    priority = f.get('priority', {}).get('name', '?')
    parent   = f.get('parent', {}) or {}
    epic     = parent.get('key', '')
    print(f'{i[\"key\"]:12} [{status:12}] [{itype:8}] [{priority:8}] {epic:12} {f[\"summary\"]}')
print(f'\n{len(issues)} issue(s) returned.')
"

#!/usr/bin/env bash
# Assign one or more tickets to an epic (by epic key or numeric Jira ID).
# Usage: jira_set_epic.sh EPIC_KEY_OR_ID REVUE-119 [REVUE-120 ...]
#
# Common epic IDs:
#   REVUE-87 (E8 — Review Intelligence & Knowledge Base) → Jira ID 10937
set -euo pipefail
source ~/.zshenv

EPIC="${1:?Usage: jira_set_epic.sh EPIC_KEY_OR_ID REVUE-XXX [...]}"
shift

# If EPIC looks like a key (e.g. REVUE-87), resolve its numeric ID first.
if [[ "$EPIC" =~ ^[A-Z]+-[0-9]+$ ]]; then
    EPIC_ID=$(curl -s \
        -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
        "https://urukia.atlassian.net/rest/api/3/issue/${EPIC}" \
        | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
    echo "Resolved ${EPIC} → Jira ID ${EPIC_ID}"
else
    EPIC_ID="$EPIC"
fi

for KEY in "$@"; do
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
        -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"fields\":{\"parent\":{\"id\":\"${EPIC_ID}\"}}}" \
        "https://urukia.atlassian.net/rest/api/3/issue/${KEY}")
    if [[ "$HTTP" == "204" ]]; then
        echo "${KEY} → ${EPIC} (HTTP 204)"
    else
        echo "ERROR: ${KEY} HTTP ${HTTP}" >&2
    fi
done

#!/usr/bin/env bash
# Transition a Jira ticket to a new status.
# Usage: jira_transition.sh REVUE-112 done|in-progress|todo
#
# Status name → ID map:
#   todo        → 10109
#   in-progress → 10110
#   done        → 10111
set -euo pipefail
source ~/.zshenv

KEY="${1:?Usage: jira_transition.sh REVUE-XXX done|in-progress|todo}"
STATUS="${2:?Provide target status: done, in-progress, or todo}"

case "${STATUS,,}" in
    done)        ID=10111 ;;
    in-progress) ID=10110 ;;
    todo)        ID=10109 ;;
    *)
        echo "Unknown status '${STATUS}'. Use: done, in-progress, todo" >&2
        exit 1
        ;;
esac

HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    -X POST "https://urukia.atlassian.net/rest/api/3/issue/${KEY}/transitions" \
    -H "Content-Type: application/json" \
    -d "{\"transition\":{\"id\":\"${ID}\"}}")

if [[ "$HTTP" == "204" ]]; then
    echo "${KEY} → ${STATUS} (HTTP 204)"
else
    echo "ERROR: HTTP ${HTTP}" >&2
    exit 1
fi

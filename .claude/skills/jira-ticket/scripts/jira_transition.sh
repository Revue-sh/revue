#!/usr/bin/env bash
# Transition a Jira ticket to a new status.
# Usage: jira_transition.sh REVUE-112 done|in-progress|todo
#
# Transition IDs (workflow-specific to urukia.atlassian.net/REVUE project):
#   todo        → 11
#   in-progress → 21
#   done        → 31
#
# Note: these are TRANSITION IDs (returned by GET .../transitions),
# not status IDs. They differ per Jira instance/project.
set -euo pipefail
source ~/.zshenv

KEY="${1:?Usage: jira_transition.sh REVUE-XXX done|in-progress|todo}"
STATUS="${2:?Provide target status: done, in-progress, or todo}"

# Portable lowercase — bash 3.2 (macOS default) does not support ${var,,}
STATUS_LOWER="$(echo "${STATUS}" | tr '[:upper:]' '[:lower:]')"

case "${STATUS_LOWER}" in
    done)         ID=31 ;;
    in-progress)  ID=21 ;;
    todo)         ID=11 ;;
    code-review)  ID=2  ;;
    rejected)     ID=3  ;;
    *)
        echo "Unknown status '${STATUS}'. Use: done, in-progress, todo, code-review, rejected" >&2
        exit 1
        ;;
esac

TMPFILE=$(mktemp)
HTTP=$(curl -s -o "${TMPFILE}" -w "%{http_code}" \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    -X POST "https://urukia.atlassian.net/rest/api/2/issue/${KEY}/transitions" \
    -H "Content-Type: application/json" \
    -d "{\"transition\":{\"id\":\"${ID}\"}}")
BODY=$(cat "${TMPFILE}")
rm -f "${TMPFILE}"

if [[ "$HTTP" == "204" ]]; then
    echo "${KEY} → ${STATUS_LOWER} (HTTP 204)"
else
    echo "ERROR: HTTP ${HTTP}" >&2
    [[ -n "${BODY}" ]] && echo "${BODY}" >&2
    exit 1
fi

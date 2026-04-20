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

KEY="${1:?Usage: jira_transition.sh REVUE-XXX <status>}"
STATUS="${2:?Provide target status: todo, in-progress, code-review, done, rejected}"

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

# Pre-flight: fetch available transitions and verify the target is reachable
AVAILABLE=$(curl -s \
    -u "${BITBUCKET_USERNAME}:${JIRA_API_TOKEN}" \
    "https://urukia.atlassian.net/rest/api/2/issue/${KEY}/transitions")

AVAILABLE_IDS=$(echo "${AVAILABLE}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(' '.join(t['id'] for t in data.get('transitions', [])))
")

if ! echo "${AVAILABLE_IDS}" | grep -qw "${ID}"; then
    echo "ERROR: Transition '${STATUS_LOWER}' (ID=${ID}) is not available from ${KEY}'s current status." >&2
    echo "" >&2
    echo "Available transitions:" >&2
    echo "${AVAILABLE}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('transitions', []):
    print(f\"  {t['id']:>3}  {t['name']}\")
" >&2
    exit 1
fi

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

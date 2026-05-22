#!/usr/bin/env bash
# Squash-merge a Bitbucket PR using the PR title as the commit message.
# Usage: merge_pr.sh PR_NUMBER
#
# Env (sourced from ~/.zshenv):
#   BITBUCKET_USERNAME     — Bitbucket username / email
#   BITBUCKET_APP_PASSWORD — App password (preferred for write calls)
#   BITBUCKET_API_TOKEN    — API token (fallback)
set -euo pipefail
source ~/.zshenv

PR_ID="${1:?Usage: merge_pr.sh PR_NUMBER}"

BB_PASS="${BITBUCKET_APP_PASSWORD:-${BITBUCKET_API_TOKEN:-}}"
if [[ -z "$BB_PASS" ]]; then
    echo "Error: set BITBUCKET_APP_PASSWORD or BITBUCKET_API_TOKEN in ~/.zshenv" >&2
    exit 1
fi
if [[ -z "${BITBUCKET_USERNAME:-}" ]]; then
    echo "Error: BITBUCKET_USERNAME not set in ~/.zshenv" >&2
    exit 1
fi

WORKSPACE="${BITBUCKET_WORKSPACE:-cbscd}"
REPO_SLUG="${BITBUCKET_REPO_SLUG:-revue}"
BASE_URL="https://api.bitbucket.org/2.0/repositories/${WORKSPACE}/${REPO_SLUG}"

# Fetch PR title — this IS the conventional commit message
PR_DATA=$(curl -s -u "${BITBUCKET_USERNAME}:${BB_PASS}" "${BASE_URL}/pullrequests/${PR_ID}")
PR_TITLE=$(jq -r '.title // empty' <<< "$PR_DATA")
SOURCE_BRANCH=$(jq -r '.source.branch.name // empty' <<< "$PR_DATA")

if [[ -z "$PR_TITLE" ]]; then
    echo "Error: could not fetch PR #${PR_ID} — check PR number and auth" >&2
    jq . <<< "$PR_DATA" >&2
    exit 1
fi

echo "PR #${PR_ID}: ${PR_TITLE}"
echo "Branch:  ${SOURCE_BRANCH}"
echo "Message: ${PR_TITLE}"
echo ""

RESPONSE=$(curl -s -X POST \
    -u "${BITBUCKET_USERNAME}:${BB_PASS}" \
    -H "Content-Type: application/json" \
    -d @- "${BASE_URL}/pullrequests/${PR_ID}/merge" <<EOF
{
    "type": "pullrequest",
    "message": $(jq -Rs . <<< "$PR_TITLE"),
    "merge_strategy": "squash",
    "close_source_branch": true
}
EOF
)

STATE=$(jq -r '.state // empty' <<< "$RESPONSE")

if [[ "$STATE" == "MERGED" ]]; then
    echo "✅ Merged: ${PR_TITLE}"
    echo "${SOURCE_BRANCH}"  # printed last so caller can capture it for branch cleanup
else
    echo "Error merging PR #${PR_ID}:" >&2
    jq . <<< "$RESPONSE" >&2
    exit 1
fi

#!/usr/bin/env bash
# Create a Bitbucket pull request.
# Usage: create_pr.sh TICKET "PR title" DESCRIPTION_FILE [destination]
#
# Examples:
#   create_pr.sh REVUE-167 "chore(tooling)[REVUE-167]: remove RuFlo" /tmp/desc.md
#   create_pr.sh REVUE-167 "chore(tooling)[REVUE-167]: remove RuFlo" /tmp/desc.md develop
#
# Env (sourced from ~/.zshenv):
#   BITBUCKET_USERNAME  — Bitbucket username / email
#   BITBUCKET_API_TOKEN — Atlassian API token (App Passwords are DEPRECATED — token only)
set -euo pipefail
source ~/.zshenv

TICKET="${1:?Usage: create_pr.sh TICKET 'PR title' description_file [destination]}"
TITLE="${2:?Provide PR title}"
DESC_FILE="${3:?Provide path to description file}"
DESTINATION="${4:-main}"

if [[ ! -f "$DESC_FILE" ]]; then
    echo "Error: description file not found: $DESC_FILE" >&2
    exit 1
fi

# Atlassian deprecated App Passwords — authenticate with the API token only.
BB_PASS="${BITBUCKET_API_TOKEN:-}"
if [[ -z "$BB_PASS" ]]; then
    echo "Error: set BITBUCKET_API_TOKEN in ~/.zshenv (App Passwords are deprecated)" >&2
    exit 1
fi
if [[ -z "${BITBUCKET_USERNAME:-}" ]]; then
    echo "Error: BITBUCKET_USERNAME not set in ~/.zshenv" >&2
    exit 1
fi

WORKSPACE="${BITBUCKET_WORKSPACE:-cbscd}"
REPO_SLUG="${BITBUCKET_REPO_SLUG:-revue}"
API_URL="https://api.bitbucket.org/2.0/repositories/${WORKSPACE}/${REPO_SLUG}/pullrequests"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
DESCRIPTION=$(cat "$DESC_FILE")

RESPONSE=$(curl -s -X POST \
    -u "${BITBUCKET_USERNAME}:${BB_PASS}" \
    -H "Content-Type: application/json" \
    -d @- "$API_URL" <<EOF
{
    "title": $(jq -Rs . <<< "$TITLE"),
    "description": $(jq -Rs . <<< "$DESCRIPTION"),
    "source": {"branch": {"name": "$BRANCH"}},
    "destination": {"branch": {"name": "$DESTINATION"}},
    "close_source_branch": true
}
EOF
)

PR_ID=$(jq -r '.id // empty' <<< "$RESPONSE")
PR_URL=$(jq -r '.links.html.href // empty' <<< "$RESPONSE")

if [[ -z "$PR_ID" ]]; then
    echo "Error creating PR:" >&2
    jq . <<< "$RESPONSE" >&2
    exit 1
fi

echo "PR #${PR_ID}: ${PR_URL}"

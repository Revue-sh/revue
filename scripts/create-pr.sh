#!/usr/bin/env bash
set -euo pipefail

# Create a Bitbucket PR with proper template formatting
# Usage: ./scripts/create-pr.sh REVUE-XX "PR title" "main"

TICKET="${1:-}"
TITLE="${2:-}"
DESTINATION="${3:-main}"

if [[ -z "$TICKET" ]] || [[ -z "$TITLE" ]]; then
    echo "Usage: $0 REVUE-XX 'PR title' [destination-branch]" >&2
    exit 1
fi

# Get current branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Bitbucket credentials from env
if [[ -z "${BITBUCKET_APP_PASSWORD:-}" ]]; then
    echo "Error: BITBUCKET_APP_PASSWORD not set" >&2
    exit 1
fi

if [[ -z "${BITBUCKET_USERNAME:-}" ]]; then
    echo "Error: BITBUCKET_USERNAME not set" >&2
    exit 1
fi

REPO_SLUG="${BITBUCKET_WORKSPACE:-cbscd}/${BITBUCKET_REPO_SLUG:-revue}"
API_URL="https://api.bitbucket.org/2.0/repositories/$REPO_SLUG/pullrequests"
JIRA_URL="https://urukia.atlassian.net/browse/$TICKET"

# Generate description (filled template will be created by caller)
DESCRIPTION_FILE="${4:-/tmp/pr-description-$TICKET.md}"

if [[ ! -f "$DESCRIPTION_FILE" ]]; then
    echo "Error: Description file not found: $DESCRIPTION_FILE" >&2
    exit 1
fi

DESCRIPTION=$(cat "$DESCRIPTION_FILE")

# Create PR via API
RESPONSE=$(curl -s -X POST \
    -u "${BITBUCKET_USERNAME:-}:$BITBUCKET_APP_PASSWORD" \
    -H "Content-Type: application/json" \
    -d @- "$API_URL" <<EOF
{
    "title": $(jq -Rs . <<< "$TITLE"),
    "description": $(jq -Rs . <<< "$DESCRIPTION"),
    "source": {
        "branch": {
            "name": "$BRANCH"
        }
    },
    "destination": {
        "branch": {
            "name": "$DESTINATION"
        }
    },
    "close_source_branch": true
}
EOF
)

# Extract PR ID and URL
PR_ID=$(jq -r '.id // empty' <<< "$RESPONSE")
PR_URL=$(jq -r '.links.html.href // empty' <<< "$RESPONSE")

if [[ -z "$PR_ID" ]]; then
    echo "Error creating PR:" >&2
    jq . <<< "$RESPONSE" >&2
    exit 1
fi

echo "✅ PR #$PR_ID created: $PR_URL"
echo "$PR_URL"

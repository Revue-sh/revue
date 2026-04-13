#!/usr/bin/env bash
# gh_post_reply.sh <parent_comment_id> <body> [pr_number] [repo]
# Posts a reply to a GitHub PR review comment thread.
# Defaults: PR #4, repo cbscd/revue-test-github
#
# Usage:
#   ./gh_post_reply.sh 3066014233 "False positive — already handled."
#   ./gh_post_reply.sh 3066014233 "Acknowledged, deferring." 4
#   ./gh_post_reply.sh 3066014233 "Acknowledged." 4 cbscd/other-repo

set -euo pipefail

PARENT_ID="${1:?Usage: gh_post_reply.sh <parent_comment_id> <body> [pr_number] [repo]}"
BODY="${2:?Usage: gh_post_reply.sh <parent_comment_id> <body> [pr_number] [repo]}"
PR_NUMBER="${3:-4}"
REPO="${4:-cbscd/revue-test-github}"

source ~/.zshenv

gh api "repos/${REPO}/pulls/${PR_NUMBER}/comments/${PARENT_ID}/replies" \
  -X POST \
  -f body="${BODY}" | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('Posted reply:', r.get('id'))
print('URL:', r.get('html_url', ''))
"

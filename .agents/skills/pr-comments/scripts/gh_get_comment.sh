#!/usr/bin/env bash
# gh_get_comment.sh <comment_id> [repo]
# Fetches the full body of a GitHub PR review comment by ID.
# Default repo: cbscd/revue-test-github
#
# Usage:
#   ./gh_get_comment.sh 3066014233
#   ./gh_get_comment.sh 3066014233 cbscd/other-repo

set -euo pipefail

COMMENT_ID="${1:?Usage: gh_get_comment.sh <comment_id> [repo]}"
REPO="${2:-cbscd/revue-test-github}"

source ~/.zshenv

gh api "repos/${REPO}/pulls/comments/${COMMENT_ID}" | python3 -c "
import json, sys
c = json.load(sys.stdin)
print('ID:   ', c.get('id'))
print('File: ', c.get('path'), 'line:', c.get('line') or c.get('original_line'))
print('User: ', c.get('user', {}).get('login'))
print()
print(c.get('body', ''))
"

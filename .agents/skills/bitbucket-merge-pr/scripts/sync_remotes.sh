#!/usr/bin/env bash
# Push local main to GitHub and GitLab after a Bitbucket merge.
# Must be called AFTER git pull origin main (local main = Bitbucket canonical HEAD).
#
# Env (sourced from ~/.zshenv):
#   GITLAB_TOKEN   — GitLab personal access token (for protect/unprotect API calls)
#
# Remotes expected:
#   github  — cbscd/revue-test-github
#   gitlab  — urukia-group/revue-test-gitlab
set -euo pipefail
source ~/.zshenv

GITLAB_PROJECT="urukia-group%2Frevue-test-gitlab"
GITLAB_API="https://gitlab.com/api/v4/projects/${GITLAB_PROJECT}"

# -- GitHub -------------------------------------------------------------------
git push github main:main --force 2>&1

# -- GitLab -------------------------------------------------------------------
# Unprotect main so we can force-push (allow_force_push is false by default)
curl -s -X DELETE \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
    "${GITLAB_API}/protected_branches/main" > /dev/null

git push gitlab main:main --force 2>&1

# Re-protect main (push + merge: Maintainer only, matching original settings)
curl -s -X POST \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"name":"main","push_access_level":40,"merge_access_level":40}' \
    "${GITLAB_API}/protected_branches" > /dev/null

echo "GitHub main synced"
echo "GitLab main synced"

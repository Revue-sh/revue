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
# Fetch so we can read what is currently on gitlab/main before overwriting it.
git fetch gitlab --quiet

# Collect any commits on gitlab/main not in local main (e.g. .gitlab-ci.yml
# tweaks that never went through Bitbucket) so we can replay them after sync.
GITLAB_ONLY_SHAS=$(git log main..gitlab/main --reverse --format="%H" 2>/dev/null || true)

# Unprotect main so we can force-push
curl -s -X DELETE \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
    "${GITLAB_API}/protected_branches/main" > /dev/null

git push gitlab main:main --force 2>&1

# Re-apply any GitLab-only commits on top (cherry-pick in original order)
if [[ -n "$GITLAB_ONLY_SHAS" ]]; then
    git switch -c tmp-gitlab-sync

    while IFS= read -r sha; do
        git cherry-pick "$sha"
    done <<< "$GITLAB_ONLY_SHAS"

    git push gitlab tmp-gitlab-sync:main
    git switch main
    git branch -D tmp-gitlab-sync
fi

# Re-protect main (push + merge: Maintainer only, matching original settings)
curl -s -X POST \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"name":"main","push_access_level":40,"merge_access_level":40}' \
    "${GITLAB_API}/protected_branches" > /dev/null

# Print success lines only after all steps have completed
echo "GitHub main synced"
echo "GitLab main synced"

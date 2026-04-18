#!/usr/bin/env bash
# Simulate the Bitbucket pipeline Revue review step locally.
#
# Usage:
#   dogfood.sh <pr_number>          # review a specific open PR
#   dogfood.sh                      # auto-detect PR from current branch
#
# Mirrors the `revue-review` step in bitbucket-pipelines.yml exactly.
set -euo pipefail
source ~/.zshenv

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

# ── 1. Resolve PR number ──────────────────────────────────────────────────────
if [[ -n "${1:-}" ]]; then
    PR_ID="$1"
else
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    echo "[dogfood] Auto-detecting PR for branch: $BRANCH"
    PR_ID=$(curl -sf \
        "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE}/revue/pullrequests?q=source.branch.name=\"${BRANCH}\"&state=OPEN" \
        -u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" \
        | python3 -c "import json,sys; prs=json.load(sys.stdin).get('values',[]); print(prs[0]['id'] if prs else '')" 2>/dev/null)

    if [[ -z "$PR_ID" ]]; then
        echo "[dogfood] ERROR: No open PR found for branch '$BRANCH'." >&2
        echo "[dogfood]        Push a PR first, or pass the PR number explicitly." >&2
        exit 1
    fi
fi

echo "[dogfood] PR: #${PR_ID} (workspace: ${BITBUCKET_WORKSPACE}/revue)"

# ── 2. Fetch PR metadata (destination branch + description) ──────────────────
PR_JSON=$(curl -sf \
    "https://api.bitbucket.org/2.0/repositories/${BITBUCKET_WORKSPACE}/revue/pullrequests/${PR_ID}" \
    -u "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}")

DEST_BRANCH=$(echo "$PR_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['destination']['branch']['name'])")
echo "[dogfood] Destination branch: $DEST_BRANCH"

DESCRIPTION_FILE=$(mktemp -t revue_pr_description)
echo "$PR_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('description',''))" > "$DESCRIPTION_FILE"
echo "[dogfood] PR description fetched ($(wc -c < "$DESCRIPTION_FILE" | tr -d ' ') bytes)"

# ── 3. Generate diff ──────────────────────────────────────────────────────────
DIFF_FILE=$(mktemp -t revue_pr_diff)
git fetch origin "$DEST_BRANCH" --quiet
git diff "origin/${DEST_BRANCH}...HEAD" > "$DIFF_FILE"
echo "[dogfood] Diff size: $(wc -l < "$DIFF_FILE" | tr -d ' ') lines"

if [[ ! -s "$DIFF_FILE" ]]; then
    echo "[dogfood] ERROR: Diff is empty. Is your branch up to date with origin?" >&2
    exit 1
fi

# ── 4. Validate config ────────────────────────────────────────────────────────
echo "[dogfood] Validating .revue.yml..."
PYTHONPATH="${REPO_ROOT}/src" python3 -m revue.cli validate --config .revue.yml
echo "[dogfood] Config OK"

# ── 5. Run review ─────────────────────────────────────────────────────────────
echo "[dogfood] Starting AI code review..."
export APP_ENV=staging
export PYTHONPATH="${REPO_ROOT}/src"

python3 -u src/revue/cli.py review \
    --diff "$DIFF_FILE" \
    --platform bitbucket \
    --pr-id "$PR_ID" \
    --workspace "$BITBUCKET_WORKSPACE" \
    --repo-slug revue \
    --bb-username "$BITBUCKET_USERNAME" \
    --bb-token "$BITBUCKET_API_TOKEN" \
    --config .revue.yml \
    --comment-style per-issue \
    --pr-description-file "$DESCRIPTION_FILE"

echo "[dogfood] Review complete"

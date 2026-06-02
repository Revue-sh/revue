#!/usr/bin/env bash
# Stop any in-progress / pending Bitbucket pipeline for a just-merged PR so we
# don't pay for an AI review (or other steps) that the merge made redundant.
#
# Usage: stop_pr_pipelines.sh PR_ID [SOURCE_BRANCH]
#
# Best-effort: if nothing is running it exits 0 quietly. The merge has already
# landed by the time this runs, so a failure here must never be treated as a
# blocker by the caller.
#
# Env (sourced from ~/.zshenv):
#   BITBUCKET_USERNAME     — Bitbucket username / email
#   BITBUCKET_APP_PASSWORD — App password (preferred for write calls)
#   BITBUCKET_API_TOKEN    — API token (fallback)
#
# Test seams (unset in production):
#   BB_PIPELINES_FIXTURE — read the pipeline-list JSON from this file, skip GET
#   BB_DRY_RUN=1         — print "WOULD STOP #<build> <uuid>" instead of POSTing
set -uo pipefail

PR_ID="${1:-}"
BRANCH="${2:-}"

if [[ -z "$PR_ID" ]]; then
    echo "Usage: stop_pr_pipelines.sh PR_ID [SOURCE_BRANCH]" >&2
    exit 1
fi
if [[ ! "$PR_ID" =~ ^[0-9]+$ ]]; then
    echo "Usage: stop_pr_pipelines.sh PR_ID [SOURCE_BRANCH] — PR_ID must be numeric, got '$PR_ID'" >&2
    exit 1
fi

WORKSPACE="${BITBUCKET_WORKSPACE:-cbscd}"
REPO_SLUG="${BITBUCKET_REPO_SLUG:-revue}"
BASE_URL="https://api.bitbucket.org/2.0/repositories/${WORKSPACE}/${REPO_SLUG}"

# --- fetch the recent pipeline list ---------------------------------------
if [[ -n "${BB_PIPELINES_FIXTURE:-}" ]]; then
    LIST_JSON="$(cat "$BB_PIPELINES_FIXTURE")"
else
    source ~/.zshenv
    BB_PASS="${BITBUCKET_APP_PASSWORD:-${BITBUCKET_API_TOKEN:-}}"
    if [[ -z "$BB_PASS" || -z "${BITBUCKET_USERNAME:-}" ]]; then
        echo "stop_pr_pipelines: BITBUCKET_USERNAME and APP_PASSWORD/API_TOKEN required" >&2
        exit 1
    fi
    # Newest first. One page (50) normally covers it — a PR's pipelines are the
    # most recent — but a high-churn merge window could push the target run past
    # page 1; if that ever bites, filter server-side via the `q` param instead.
    LIST_JSON="$(curl -s -u "${BITBUCKET_USERNAME}:${BB_PASS}" \
        "${BASE_URL}/pipelines/?sort=-created_on&pagelen=50")"
    if [[ -z "$LIST_JSON" ]] || ! jq -e . >/dev/null 2>&1 <<< "$LIST_JSON"; then
        echo "stop_pr_pipelines: could not list pipelines (network/auth/API error)" >&2
        jq -r '.error.message // empty' <<< "$LIST_JSON" 2>/dev/null >&2 || true
        exit 1
    fi
fi

# --- select still-running pipelines belonging to this PR / branch ---------
# Match a PR pipeline by pullrequest id, or by its source branch when given
# (PR targets carry the branch in `.target.source`); match a ref/branch
# pipeline by `.target.ref_name`. Only PENDING / IN_PROGRESS are stoppable.
# No 2>/dev/null here: a jq failure must surface, not masquerade as "nothing
# to stop" and silently let the pipeline keep billing.
SELECTED="$(jq -r \
    --argjson pr "$PR_ID" \
    --arg branch "$BRANCH" '
    .values[]
    | select(.state.name == "IN_PROGRESS" or .state.name == "PENDING")
    | select(
        (.target.pullrequest.id == $pr)
        or ($branch != "" and (.target.source == $branch or .target.ref_name == $branch))
      )
    | "\(.build_number)\t\(.uuid)"' <<< "$LIST_JSON")"

if [[ -z "$SELECTED" ]]; then
    echo "No in-progress pipelines to stop for PR #${PR_ID}."
    exit 0
fi

# --- stop each ------------------------------------------------------------
FAILED=0
while IFS=$'\t' read -r BUILD UUID; do
    [[ -z "$UUID" ]] && continue
    if [[ "${BB_DRY_RUN:-}" == "1" ]]; then
        echo "WOULD STOP #${BUILD} ${UUID}"
        continue
    fi
    # uuid carries literal braces — URL-encode them for the path segment.
    ENC="${UUID//\{/%7B}"; ENC="${ENC//\}/%7D}"
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        -u "${BITBUCKET_USERNAME}:${BB_PASS}" \
        "${BASE_URL}/pipelines/${ENC}/stopPipeline")"
    if [[ "$CODE" == "204" || "$CODE" == "200" ]]; then
        echo "✅ Stopped #${BUILD} ${UUID}"
    else
        echo "⚠️  Failed to stop #${BUILD} ${UUID} (HTTP ${CODE})" >&2
        FAILED=$((FAILED+1))
    fi
done <<< "$SELECTED"

[[ $FAILED -eq 0 ]] || exit 5

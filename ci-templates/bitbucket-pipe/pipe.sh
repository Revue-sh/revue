#!/usr/bin/env bash
# Revue.io Bitbucket Pipe entrypoint
# Fetches the PR diff via the Bitbucket API, runs the Revue review pipeline,
# and posts inline findings + a summary comment back to the PR.
set -euo pipefail

DIFF_FILE="/tmp/revue_pr.diff"
REVIEW_JSON="/tmp/revue_review.json"

# ── 1. Validate required inputs ─────────────────────────────────────────────

if [[ -z "${AI_API_KEY:-}" ]]; then
  echo "ERROR: AI_API_KEY is required. Add your AI provider key as a secured repository variable."
  exit 1
fi

if [[ -z "${BITBUCKET_USERNAME:-}" ]]; then
  echo "ERROR: BITBUCKET_USERNAME is required."
  exit 1
fi

if [[ -z "${BITBUCKET_API_TOKEN:-}" ]]; then
  echo "ERROR: BITBUCKET_API_TOKEN is required."
  exit 1
fi

# ── 2. Resolve PR ID from Bitbucket pipeline variables ──────────────────────
# Bitbucket Pipelines injects BITBUCKET_PR_ID for pull-request pipelines.

PR_ID="${BITBUCKET_PR_ID:-}"
WORKSPACE="${BITBUCKET_WORKSPACE:-}"
REPO_SLUG="${BITBUCKET_REPO_SLUG:-}"
COMMIT_SHA="${BITBUCKET_COMMIT:-}"

if [[ -z "${PR_ID}" ]]; then
  echo "ERROR: BITBUCKET_PR_ID is not set — this pipe must run in a pull-request pipeline."
  echo "  Add a 'pull-requests' trigger section to your bitbucket-pipelines.yml."
  exit 1
fi

echo "Revue.io — reviewing PR #${PR_ID} in ${WORKSPACE}/${REPO_SLUG} (commit ${COMMIT_SHA})"

# ── 3. Post INPROGRESS status ────────────────────────────────────────────────

post_status() {
  local state="$1"
  local description="$2"
  local auth
  auth=$(echo -n "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" | base64)
  curl -s -X POST \
    "https://api.bitbucket.org/2.0/repositories/${WORKSPACE}/${REPO_SLUG}/commit/${COMMIT_SHA}/statuses/build" \
    -H "Authorization: Basic ${auth}" \
    -H "Content-Type: application/json" \
    -d "{
      \"key\": \"revue-io\",
      \"state\": \"${state}\",
      \"name\": \"Revue.io AI Review\",
      \"description\": \"${description}\",
      \"url\": \"https://revue-io.fly.dev\"
    }" > /dev/null || true
}

post_status "INPROGRESS" "Revue.io review running..."

# ── 4. Fetch PR diff from Bitbucket API ─────────────────────────────────────

echo "Fetching PR diff..."
auth=$(echo -n "${BITBUCKET_USERNAME}:${BITBUCKET_API_TOKEN}" | base64)
HTTP_STATUS=$(curl -s -w "%{http_code}" -o "${DIFF_FILE}" \
  -H "Authorization: Basic ${auth}" \
  -H "Accept: text/plain" \
  "https://api.bitbucket.org/2.0/repositories/${WORKSPACE}/${REPO_SLUG}/pullrequests/${PR_ID}/diff")

if [[ "${HTTP_STATUS}" != "200" ]]; then
  echo "ERROR: Failed to fetch PR diff (HTTP ${HTTP_STATUS})."
  post_status "FAILED" "Could not fetch PR diff"
  exit 1
fi

if [[ ! -s "${DIFF_FILE}" ]]; then
  echo "Revue — empty diff, no review needed."
  post_status "SUCCESSFUL" "No changes to review"
  exit 0
fi

DIFF_LINES=$(wc -l < "${DIFF_FILE}")
echo "Diff fetched: ${DIFF_LINES} lines"

# ── 5. Run Revue review ─────────────────────────────────────────────────────

echo "Running Revue.io review..."

REVUE_ARGS=(
  review
  --diff "${DIFF_FILE}"
  --format json
  --output "${REVIEW_JSON}"
  --provider "${AI_PROVIDER:-anthropic}"
  --model "${AI_MODEL:-claude-sonnet-4-5-20250929}"
  --mode "${MODE:-multi-agent}"
  --min-confidence "${MIN_CONFIDENCE:-70}"
  --platform bitbucket
  --pr-id "${PR_ID}"
  --workspace "${WORKSPACE}"
  --repo-slug "${REPO_SLUG}"
  --bb-username "${BITBUCKET_USERNAME}"
  --bb-token "${BITBUCKET_API_TOKEN}"
)

if [[ -n "${AI_BASE_URL:-}" ]]; then
  REVUE_ARGS+=(--base-url "${AI_BASE_URL}")
fi

if [[ -f "${CONFIG_PATH:-.revue.yml}" ]]; then
  REVUE_ARGS+=(--config "${CONFIG_PATH:-.revue.yml}")
fi

if [[ -n "${REVUE_TOKEN:-}" ]]; then
  REVUE_ARGS+=(--token "${REVUE_TOKEN}")
fi

AI_API_KEY="${AI_API_KEY}" revue "${REVUE_ARGS[@]}" || {
  echo "WARNING: Revue review did not complete — check AI_API_KEY and network access."
  post_status "FAILED" "Review pipeline error"
  exit 0
}

# ── 6. Parse results ─────────────────────────────────────────────────────────

FINDINGS_COUNT=0
CRITICAL_COUNT=0

if [[ -f "${REVIEW_JSON}" ]]; then
  FINDINGS_COUNT=$(python3 -c "
import json
try:
    data = json.load(open('${REVIEW_JSON}'))
    print(len(data.get('findings', [])))
except Exception:
    print(0)
")
  CRITICAL_COUNT=$(python3 -c "
import json
try:
    data = json.load(open('${REVIEW_JSON}'))
    print(sum(1 for f in data.get('findings', []) if f.get('severity') == 'critical'))
except Exception:
    print(0)
")
fi

echo "Revue.io — ${FINDINGS_COUNT} findings (${CRITICAL_COUNT} critical)"

# ── 7. Post final commit status ──────────────────────────────────────────────

if [[ "${CRITICAL_COUNT}" -gt 0 ]]; then
  post_status "FAILED" "Revue found ${CRITICAL_COUNT} critical issue(s)"
else
  post_status "SUCCESSFUL" "Revue review complete — ${FINDINGS_COUNT} finding(s)"
fi

# ── 8. Fail pipeline if requested ────────────────────────────────────────────

if [[ "${FAIL_ON_CRITICAL:-false}" == "true" ]] && [[ "${CRITICAL_COUNT}" -gt 0 ]]; then
  echo "ERROR: Revue found ${CRITICAL_COUNT} critical issue(s). Set FAIL_ON_CRITICAL: false to allow merge."
  exit 1
fi

exit 0

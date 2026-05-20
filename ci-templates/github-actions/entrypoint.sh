#!/usr/bin/env bash
# Revue action entrypoint
# Runs the Revue review pipeline and posts findings to the PR.
# Called by action.yml — do not invoke directly.
set -euo pipefail

DIFF_FILE="${DIFF_FILE:-/tmp/revue_pr.diff}"
REVIEW_JSON="/tmp/revue_review.json"

# ── 1. Validate inputs ──────────────────────────────────────────────────────

if [[ -z "${AI_API_KEY:-}" ]]; then
  echo "::error::ai_api_key is required. Add your AI provider API key as a secret."
  exit 1
fi

if [[ ! -f "${DIFF_FILE}" ]] || [[ ! -s "${DIFF_FILE}" ]]; then
  echo "::notice::Revue — diff file empty or missing, skipping review."
  echo "findings_count=0" >> "$GITHUB_OUTPUT"
  echo "critical_count=0" >> "$GITHUB_OUTPUT"
  echo "review_url=" >> "$GITHUB_OUTPUT"
  exit 0
fi

# ── 2. Run Revue review ─────────────────────────────────────────────────────

echo "::group::Revue — running review"

REVUE_ARGS=(
  review
  --diff "${DIFF_FILE}"
  --format json
  --output "${REVIEW_JSON}"
  --provider "${REVUE_PROVIDER:-openrouter}"
  --model "${REVUE_MODEL:-deepseek/deepseek-v4-pro}"
  --mode "${REVUE_MODE:-multi-agent}"
  --min-confidence "${REVUE_MIN_CONFIDENCE:-70}"
)

if [[ -n "${REVUE_BASE_URL:-}" ]]; then
  REVUE_ARGS+=(--base-url "${REVUE_BASE_URL}")
fi

if [[ -f "${REVUE_CONFIG:-.revue.yml}" ]]; then
  REVUE_ARGS+=(--config "${REVUE_CONFIG:-.revue.yml}")
fi

if [[ -n "${REVUE_TOKEN:-}" ]]; then
  REVUE_ARGS+=(--token "${REVUE_TOKEN}")
fi

AI_API_KEY="${AI_API_KEY}" revue "${REVUE_ARGS[@]}" || {
  echo "::warning::Revue review did not complete — check AI_API_KEY and network access."
  echo "findings_count=0" >> "$GITHUB_OUTPUT"
  echo "critical_count=0" >> "$GITHUB_OUTPUT"
  echo "review_url=" >> "$GITHUB_OUTPUT"
  exit 0
}

echo "::endgroup::"

# ── 3. Parse results and emit outputs ───────────────────────────────────────

if [[ ! -f "${REVIEW_JSON}" ]]; then
  echo "::warning::Revue — no review output produced."
  echo "findings_count=0" >> "$GITHUB_OUTPUT"
  echo "critical_count=0" >> "$GITHUB_OUTPUT"
  echo "review_url=" >> "$GITHUB_OUTPUT"
  exit 0
fi

FINDINGS_COUNT=$(python3 -c "
import json, sys
try:
    data = json.load(open('${REVIEW_JSON}'))
    print(len(data.get('findings', [])))
except Exception:
    print(0)
")

CRITICAL_COUNT=$(python3 -c "
import json, sys
try:
    data = json.load(open('${REVIEW_JSON}'))
    print(sum(1 for f in data.get('findings', []) if f.get('severity') == 'critical'))
except Exception:
    print(0)
")

REVIEW_URL=$(python3 -c "
import json, sys
try:
    data = json.load(open('${REVIEW_JSON}'))
    print(data.get('review_url', ''))
except Exception:
    print('')
")

echo "findings_count=${FINDINGS_COUNT}" >> "$GITHUB_OUTPUT"
echo "critical_count=${CRITICAL_COUNT}" >> "$GITHUB_OUTPUT"
echo "review_url=${REVIEW_URL}" >> "$GITHUB_OUTPUT"

echo "::notice::Revue — ${FINDINGS_COUNT} findings (${CRITICAL_COUNT} critical)"

# ── 4. Fail pipeline if requested and critical findings exist ────────────────

if [[ "${REVUE_FAIL_ON_CRITICAL:-false}" == "true" ]] && [[ "${CRITICAL_COUNT}" -gt 0 ]]; then
  echo "::error::Revue found ${CRITICAL_COUNT} critical issue(s). Set fail_on_critical: false to allow merge."
  exit 1
fi

#!/usr/bin/env bash
# run-comparison.sh — Run a baseline + contextual review comparison locally.
#
# Usage:
#   ./scripts/run-comparison.sh REVUE-XX [pr_description_file]
#
# Examples:
#   ./scripts/run-comparison.sh REVUE-86                        # no context (baseline only)
#   ./scripts/run-comparison.sh REVUE-86 /tmp/pr_description.txt  # both runs
#
# Output: docs/review-comparisons/REVUE-XX/{baseline,contextual}.json + ANALYSIS.md
#
# Requirements:
#   - AI_API_KEY set in environment (or ~/.zshenv)
#   - git diff already generated (or pass DIFF_FILE env var)

set -euo pipefail

TICKET="${1:-}"
PR_DESC_FILE="${2:-}"
DIFF_FILE="${DIFF_FILE:-/tmp/revue_compare.diff}"
OUT_DIR="docs/review-comparisons/${TICKET}"

if [[ -z "$TICKET" ]]; then
    echo "Usage: $0 REVUE-XX [pr_description_file]" >&2
    exit 1
fi

# Source env
source ~/.zshenv 2>/dev/null || true
source ~/.zshrc 2>/dev/null || true

if [[ -z "${AI_API_KEY:-}" ]]; then
    echo "ERROR: AI_API_KEY not set. Export it or add to ~/.zshenv." >&2
    exit 1
fi

DEST_BRANCH="${DEST_BRANCH:-main}"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "==> Generating diff: ${CURRENT_BRANCH} vs ${DEST_BRANCH}"
git diff "origin/${DEST_BRANCH}...HEAD" > "${DIFF_FILE}"
echo "    Diff size: $(wc -l < "${DIFF_FILE}") lines"

mkdir -p "${OUT_DIR}"

# ── Baseline (no context) ───────────────────────────────────────────────────
echo ""
echo "==> Running BASELINE review (no PR context)..."
PYTHONPATH="$(pwd)/src" python3 src/revue/cli.py review \
    --diff "${DIFF_FILE}" \
    --provider anthropic \
    --model "${AI_MODEL:-claude-sonnet-4-5}" \
    --config .revue.yml \
    --output json \
    2>/dev/null \
    > "${OUT_DIR}/baseline.json"
echo "    Saved: ${OUT_DIR}/baseline.json"

# ── Contextual (with PR description) ───────────────────────────────────────
if [[ -n "${PR_DESC_FILE}" && -f "${PR_DESC_FILE}" ]]; then
    echo ""
    echo "==> Running CONTEXTUAL review (with PR description from ${PR_DESC_FILE})..."
    cp "${PR_DESC_FILE}" "${OUT_DIR}/pr_description.txt"
    PYTHONPATH="$(pwd)/src" python3 src/revue/cli.py review \
        --diff "${DIFF_FILE}" \
        --provider anthropic \
        --model "${AI_MODEL:-claude-sonnet-4-5}" \
        --config .revue.yml \
        --output json \
        --pr-description-file "${PR_DESC_FILE}" \
        2>/dev/null \
        > "${OUT_DIR}/contextual.json"
    echo "    Saved: ${OUT_DIR}/contextual.json"

    # ── Analysis ───────────────────────────────────────────────────────────
    echo ""
    echo "==> Generating analysis..."
    python3 scripts/compare_reviews.py "${OUT_DIR}"
else
    echo ""
    echo "    No PR description file provided — skipping contextual run."
    echo "    Re-run with: $0 ${TICKET} /path/to/pr_description.txt"
fi

echo ""
echo "Done. Results in ${OUT_DIR}/"

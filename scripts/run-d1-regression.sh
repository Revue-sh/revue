#!/usr/bin/env bash
# run-d1-regression.sh — Compare Revue review quality pre- and post-D1 prompt restructure.
#
# Usage:
#   ./scripts/run-d1-regression.sh
#
# Requires:
#   - ANTHROPIC_API_KEY set in environment (or ~/.zshenv / ~/.zshrc)
#   - Git working tree clean (no uncommitted changes that conflict with worktree)
#
# Output: docs/review-comparisons/REVUE-152/{pre-d1,post-d1}-{small,medium,large}.json + ANALYSIS.md
#
# IMPORTANT: Uses git worktree to isolate pre-D1 code — never git stash.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PRE_D1_SHA="73543880"
WORKTREE_PATH="/tmp/revue-pre-d1"
FIXTURE_TMP="/tmp/revue_d1_fixtures"
OUT_DIR="${REPO_ROOT}/docs/review-comparisons/REVUE-152"
MODEL="${AI_MODEL:-claude-haiku-4-5-20251001}"

# Source env
source ~/.zshenv 2>/dev/null || true

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY not set. Export it or add to ~/.zshenv." >&2
    exit 1
fi

export APP_ENV=development
export REVUE_TIER_OVERRIDE=pro
# Unset Claude Code keys that shadow the project's AI_API_KEY
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY
export REVUE_API_KEY_ENV=AI_API_KEY

# ── Prepare ────────────────────────────────────────────────────────────────
mkdir -p "${OUT_DIR}"
mkdir -p "${FIXTURE_TMP}"

echo "==> Copying fixtures to ${FIXTURE_TMP}/"
cp "${REPO_ROOT}/src/revue/tests/fixtures/diffs/small.diff"  "${FIXTURE_TMP}/small.diff"
cp "${REPO_ROOT}/src/revue/tests/fixtures/diffs/medium.diff" "${FIXTURE_TMP}/medium.diff"
cp "${REPO_ROOT}/src/revue/tests/fixtures/diffs/large.diff"  "${FIXTURE_TMP}/large.diff"

# ── Pre-D1 worktree ────────────────────────────────────────────────────────
echo ""
echo "==> Creating pre-D1 worktree at ${WORKTREE_PATH} (SHA: ${PRE_D1_SHA})"

# Remove stale worktree if it exists
if [[ -d "${WORKTREE_PATH}" ]]; then
    git worktree remove "${WORKTREE_PATH}" --force 2>/dev/null || rm -rf "${WORKTREE_PATH}"
    git worktree prune 2>/dev/null || true
fi

git worktree add "${WORKTREE_PATH}" "${PRE_D1_SHA}"

echo ""
echo "==> Running pre-D1 reviews (model: ${MODEL})"

for SIZE in small medium large; do
    echo "    [pre-D1] ${SIZE}..."
    PYTHONPATH="${WORKTREE_PATH}/src" python3 "${WORKTREE_PATH}/src/revue/cli.py" review \
        --diff "${FIXTURE_TMP}/${SIZE}.diff" \
        --provider anthropic \
        --model "${MODEL}" \
        --config "${WORKTREE_PATH}/.revue.yml" \
        --output json \
        2>/dev/null \
        | grep -v "^\[revue\]" \
        > "${OUT_DIR}/pre-d1-${SIZE}.json"
    echo "    Saved: ${OUT_DIR}/pre-d1-${SIZE}.json"
done

# ── Remove worktree ────────────────────────────────────────────────────────
echo ""
echo "==> Removing pre-D1 worktree"
git worktree remove "${WORKTREE_PATH}" --force
git worktree prune 2>/dev/null || true

# ── Post-D1 (HEAD) reviews ─────────────────────────────────────────────────
echo ""
echo "==> Running post-D1 reviews (model: ${MODEL})"

for SIZE in small medium large; do
    echo "    [post-D1] ${SIZE}..."
    PYTHONPATH="${REPO_ROOT}/src" python3 "${REPO_ROOT}/src/revue/cli.py" review \
        --diff "${FIXTURE_TMP}/${SIZE}.diff" \
        --provider anthropic \
        --model "${MODEL}" \
        --config "${REPO_ROOT}/.revue.yml" \
        --output json \
        2>/dev/null \
        | grep -v "^\[revue\]" \
        > "${OUT_DIR}/post-d1-${SIZE}.json"
    echo "    Saved: ${OUT_DIR}/post-d1-${SIZE}.json"
done

# ── Analysis ───────────────────────────────────────────────────────────────
echo ""
echo "==> Generating ANALYSIS.md"
python3 "${REPO_ROOT}/scripts/compare_d1_reviews.py" "${OUT_DIR}"

echo ""
echo "Done. Results in ${OUT_DIR}/"
echo "  pre-d1-{small,medium,large}.json"
echo "  post-d1-{small,medium,large}.json"
echo "  ANALYSIS.md"

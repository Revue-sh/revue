#!/usr/bin/env bash
# Run the Revue AI review locally against the current branch.
#
# Usage:
#   dogfood.sh              # diff vs origin/main
#   dogfood.sh <base>       # diff vs origin/<base>
#
# Findings are printed here. Nothing is posted anywhere.
# Run this before opening a PR — fix issues, then open the PR clean.
set -euo pipefail
source ~/.zshenv

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

BASE_BRANCH="${1:-main}"

# ── 1. Generate diff ──────────────────────────────────────────────────────────
echo "[dogfood] Diffing origin/${BASE_BRANCH}...HEAD"
git fetch origin "$BASE_BRANCH" --quiet
DIFF_FILE=$(mktemp -t revue_pr_diff)
git diff "origin/${BASE_BRANCH}...HEAD" > "$DIFF_FILE"
echo "[dogfood] Diff: $(wc -l < "$DIFF_FILE" | tr -d ' ') lines"

if [[ ! -s "$DIFF_FILE" ]]; then
    echo "[dogfood] Diff is empty — nothing to review." >&2
    exit 0
fi

# ── 2. Validate config ────────────────────────────────────────────────────────
# REVUE-310: CLI now lives in packaging/revue-ci/src/revue_ci/cli.py
PY="${REPO_ROOT}/.venv/bin/python3"
"$PY" -m revue_ci.cli validate --config .revue.yml

# ── 3. Run review ─────────────────────────────────────────────────────────────
echo "[dogfood] Starting AI code review..."
export APP_ENV=staging

"$PY" -u -m revue_ci.cli review \
    --diff "$DIFF_FILE" \
    --config .revue.yml \
    --comment-style per-issue

echo "[dogfood] Done"

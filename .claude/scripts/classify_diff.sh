#!/usr/bin/env bash
# Classify a git diff into TRIVIAL / MEDIUM / HIGH.
#
# Usage:  bash .claude/scripts/classify_diff.sh [base-ref]
#         Defaults to origin/main.  Pass HEAD for uncommitted changes.
#
# Exit:   0 = TRIVIAL   (auto-merge on green CI; no review)
#         1 = MEDIUM    (in-session adversarial review; auto-merge on clean pass)
#         2 = HIGH      (human Step-10 gate mandatory; no exceptions)
#
# Fail-upward rule: any file matching no pattern → HIGH immediately.

set -euo pipefail

# Guard: must be inside a git repository.  Fail HIGH for safety if not.
if ! git rev-parse --git-dir &>/dev/null; then
    echo "ERROR: not inside a git repository — failing HIGH for safety" >&2
    exit 2
fi

BASE="${1:-origin/main}"

# Collect changed files from branch diff, falling back to staged/uncommitted.
CHANGED_FILES=$(git diff --name-only "${BASE}...HEAD" 2>/dev/null) || true
if [ -z "$CHANGED_FILES" ]; then
    CHANGED_FILES=$(git diff --name-only "${BASE}" 2>/dev/null) || true
fi
if [ -z "$CHANGED_FILES" ]; then
    CHANGED_FILES=$(git diff --name-only --cached 2>/dev/null) || true
fi
if [ -z "$CHANGED_FILES" ]; then
    CHANGED_FILES=$(git diff --name-only 2>/dev/null) || true
fi

if [ -z "$CHANGED_FILES" ]; then
    echo "TRIVIAL (no changes detected)"
    exit 0
fi

# ── HIGH path patterns ────────────────────────────────────────────────────────
# The publish-path gate is ABSOLUTE: any matching file → HIGH, no exceptions.
# Add new publish/security paths here; never reclassify downward without an
# explicit human decision.
HIGH_PATTERNS=(
    "packaging/"
    "bitbucket-pipelines.yml"
    ".github/workflows/"
    ".gitlab-ci.yml"
    "src/web/jwt_"
    "src/web/rate_limiter.py"
    "src/web/routes/api_routes.py"
    "src/web/database.py"
    "src/web/billing.py"
    "src/web/stripe"
    "src/web/main.py"
    "revue_core/validate.py"
    "revue_core/cache_paths.py"
    "revue_core/security/"
    "db/repositories/"
    "db/migrations/"
)

# ── MEDIUM path patterns ──────────────────────────────────────────────────────
# Files matching these get in-session adversarial review (no human gate).
# FAIL-UPWARD: any file not matching HIGH, MEDIUM, or TRIVIAL → HIGH.
MEDIUM_PATTERNS=(
    "src/"
    "tests/"
    ".claude/"
    "scripts/"
)

# ── TRIVIAL path patterns ─────────────────────────────────────────────────────
# Docs-only: no executable logic, no review needed.
TRIVIAL_PATTERNS=(
    "docs/"
    "_bmad-output/"
)

tier="TRIVIAL"

while IFS= read -r file; do
    [ -z "$file" ] && continue

    # ── HIGH: hard patterns (short-circuit immediately) ───────────────────────
    for pat in "${HIGH_PATTERNS[@]}"; do
        case "$file" in
            *"$pat"*)
                echo "HIGH — publish/security path: $file (matched: $pat)"
                exit 2
                ;;
        esac
    done

    # ── HIGH: fly.*.toml glob — catches any Fly.io config, not just enumerated ──
    case "$file" in
        fly*.toml|*/fly*.toml)
            echo "HIGH — fly config: $file"
            exit 2
            ;;
    esac

    # ── MEDIUM: known behavioral paths ───────────────────────────────────────
    for pat in "${MEDIUM_PATTERNS[@]}"; do
        case "$file" in
            *"$pat"*)
                tier="MEDIUM"
                continue 2
                ;;
        esac
    done

    # ── TRIVIAL: docs-only paths ──────────────────────────────────────────────
    for pat in "${TRIVIAL_PATTERNS[@]}"; do
        case "$file" in
            *"$pat"*)
                continue 2
                ;;
        esac
    done

    # ── No pattern matched → fail upward to HIGH ──────────────────────────────
    echo "HIGH — no pattern matched (fails upward): $file"
    exit 2

done <<< "$CHANGED_FILES"

case "$tier" in
    TRIVIAL)
        echo "TRIVIAL"
        exit 0
        ;;
    MEDIUM)
        echo "MEDIUM"
        exit 1
        ;;
esac

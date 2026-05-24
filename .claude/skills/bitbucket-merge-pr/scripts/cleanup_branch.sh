#!/usr/bin/env bash
# Post-merge cleanup: remove worktree (if any) attached to BRANCH, then delete BRANCH.
# Usage: cleanup_branch.sh BRANCH [--repo PATH]
#
# Behaviour:
#   - If BRANCH is checked out in a worktree with uncommitted changes → exits non-zero, preserves both.
#   - If BRANCH is checked out in a clean worktree → removes the worktree, then deletes the branch.
#   - If BRANCH has no worktree → just deletes the branch.

set -uo pipefail

BRANCH="${1:-}"
REPO="."
if [[ "${2:-}" == "--repo" ]]; then
    REPO="${3:-.}"
fi

if [[ -z "$BRANCH" ]]; then
    echo "Usage: cleanup_branch.sh BRANCH [--repo PATH]" >&2
    exit 1
fi

# M2 fix: catch the common --repo-first mistake before it hits git.
if [[ "$BRANCH" == --* ]]; then
    echo "Usage: cleanup_branch.sh BRANCH [--repo PATH]" >&2
    echo "BRANCH must come first; got: $BRANCH" >&2
    exit 1
fi

# Parse `git worktree list --porcelain` to find the path (if any) checked out at BRANCH.
WT_PATH=$(git -C "$REPO" worktree list --porcelain | awk -v br="refs/heads/$BRANCH" '
    /^worktree / { p = substr($0, 10) }
    $0 == "branch " br { print p; exit }
')

if [[ -n "$WT_PATH" ]]; then
    # Refuse to nuke a dirty worktree.
    if [[ -n "$(git -C "$WT_PATH" status --porcelain 2>/dev/null)" ]]; then
        echo "❌ Worktree $WT_PATH has uncommitted/dirty changes — refusing to remove." >&2
        echo "   Commit, stash, or discard them, then re-run." >&2
        exit 2
    fi
    if ! git -C "$REPO" worktree remove "$WT_PATH" 2>&1; then
        echo "❌ Failed to remove worktree $WT_PATH" >&2
        exit 3
    fi
    echo "✅ Worktree $WT_PATH removed"
fi

BRANCH_ERR=$(git -C "$REPO" branch -d "$BRANCH" 2>&1)
RC=$?
if [[ $RC -eq 0 ]]; then
    echo "✅ Branch $BRANCH deleted"
else
    echo "❌ Branch $BRANCH not deleted:" >&2
    echo "   $BRANCH_ERR" >&2
    # Only suggest the squash-merge framing when stderr actually says so.
    # Other failures (no such branch, not a git repo, etc.) surface verbatim.
    if grep -q "not fully merged" <<< "$BRANCH_ERR"; then
        echo "   Likely squash-merge. Inspect: git diff origin/main..$BRANCH --numstat" >&2
        echo "   Force-delete if safe: git branch -D $BRANCH" >&2
    fi
    exit 4
fi

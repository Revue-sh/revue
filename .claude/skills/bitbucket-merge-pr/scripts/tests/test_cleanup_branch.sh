#!/usr/bin/env bash
# Test cleanup_branch.sh against the three TCs from REVUE-349.
# Self-contained: creates temp git repos, runs the script, verifies effects.
# Run: bash .claude/skills/bitbucket-merge-pr/scripts/tests/test_cleanup_branch.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEANUP="${SCRIPT_DIR}/cleanup_branch.sh"

PASS=0
FAIL=0

_setup_repo() {
    local tmp
    tmp="$(mktemp -d)"
    git -C "$tmp" init -q -b main
    git -C "$tmp" -c user.email=t@t -c user.name=t commit --allow-empty -q -m "init"
    git -C "$tmp" branch feat/test-branch
    echo "$tmp"
}

_pass() { PASS=$((PASS+1)); echo "  ✅ $1"; }
_fail() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

# TC1: branch with worktree → both removed
echo "TC1: branch with worktree"
REPO="$(_setup_repo)"
WT="${REPO}-wt"
git -C "$REPO" worktree add -q "$WT" feat/test-branch
OUT="$(bash "$CLEANUP" feat/test-branch --repo "$REPO" 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && _pass "exit 0" || _fail "exit $RC (expected 0)"
[[ ! -d "$WT" ]] && _pass "worktree dir removed" || _fail "worktree dir still exists"
! git -C "$REPO" worktree list | grep -q "$WT" && _pass "worktree unregistered" || _fail "worktree still listed"
! git -C "$REPO" branch | grep -q feat/test-branch && _pass "branch deleted" || _fail "branch still exists"
echo "$OUT" | grep -q "Worktree.*removed" && _pass "output reports worktree removed" || _fail "no worktree-removed line in output"
echo "$OUT" | grep -q "Branch.*deleted" && _pass "output reports branch deleted" || _fail "no branch-deleted line in output"
rm -rf "$REPO" "$WT"

# TC2: branch without worktree → just branch removed
echo "TC2: branch without worktree"
REPO="$(_setup_repo)"
OUT="$(bash "$CLEANUP" feat/test-branch --repo "$REPO" 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && _pass "exit 0" || _fail "exit $RC (expected 0)"
! git -C "$REPO" branch | grep -q feat/test-branch && _pass "branch deleted" || _fail "branch still exists"
echo "$OUT" | grep -q "Branch.*deleted" && _pass "output reports branch deleted" || _fail "no branch-deleted line in output"
! echo "$OUT" | grep -q "Worktree.*removed" && _pass "no worktree-removed line (none existed)" || _fail "spurious worktree-removed line"
rm -rf "$REPO"

# TC3: dirty worktree → script errors and stops
echo "TC3: dirty worktree"
REPO="$(_setup_repo)"
WT="${REPO}-wt"
git -C "$REPO" worktree add -q "$WT" feat/test-branch
echo "uncommitted" > "$WT/dirty.txt"
OUT="$(bash "$CLEANUP" feat/test-branch --repo "$REPO" 2>&1)"
RC=$?
[[ $RC -ne 0 ]] && _pass "exit non-zero" || _fail "exit 0 (expected non-zero)"
[[ -d "$WT" ]] && _pass "worktree preserved" || _fail "worktree force-removed (should not)"
git -C "$REPO" branch | grep -q feat/test-branch && _pass "branch preserved" || _fail "branch deleted (should not)"
echo "$OUT" | grep -qi "dirty\|uncommitted" && _pass "error mentions dirty/uncommitted" || _fail "error doesn't explain dirty state"
rm -rf "$REPO" "$WT"

# TC4 (L2): prefix-collision — feat/REVUE-X must not match feat/REVUE-X-extended
echo "TC4: branch prefix collision"
REPO="$(_setup_repo)"
git -C "$REPO" branch feat/REVUE-X
git -C "$REPO" branch feat/REVUE-X-extended
WT="${REPO}-wt"
git -C "$REPO" worktree add -q "$WT" feat/REVUE-X-extended
OUT="$(bash "$CLEANUP" feat/REVUE-X --repo "$REPO" 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && _pass "exit 0 (no worktree match)" || _fail "exit $RC (expected 0)"
[[ -d "$WT" ]] && _pass "extended-branch worktree untouched" || _fail "extended-branch worktree removed by mistake"
! git -C "$REPO" branch | grep -q "^  feat/REVUE-X$" && _pass "feat/REVUE-X deleted" || _fail "feat/REVUE-X not deleted"
git -C "$REPO" branch | grep -q feat/REVUE-X-extended && _pass "feat/REVUE-X-extended preserved" || _fail "feat/REVUE-X-extended deleted"
rm -rf "$REPO" "$WT"

# TC5 (M1): exit-4 surfaces git's stderr verbatim, doesn't hardcode squash-merge framing
echo "TC5: non-squash branch-d failure surfaces real error"
REPO="$(_setup_repo)"
OUT="$(bash "$CLEANUP" branch-that-does-not-exist --repo "$REPO" 2>&1)"
RC=$?
[[ $RC -eq 4 ]] && _pass "exit 4" || _fail "exit $RC (expected 4)"
! echo "$OUT" | grep -qi "squash" && _pass "no spurious squash-merge claim" || _fail "wrongly mentions squash-merge"
echo "$OUT" | grep -qi "not found\|no such\|does not exist" && _pass "surfaces git's real error" || _fail "git error not surfaced"
rm -rf "$REPO"

# TC6 (M2): --repo first → clear error, not silent misclassification
echo "TC6: --repo passed before BRANCH is rejected"
OUT="$(bash "$CLEANUP" --repo /tmp feat/foo 2>&1)"
RC=$?
[[ $RC -eq 1 ]] && _pass "exit 1 (usage error)" || _fail "exit $RC (expected 1)"
echo "$OUT" | grep -q "BRANCH must come first" && _pass "explains the mistake" || _fail "doesn't explain misordering"

echo ""
echo "===== Results: ${PASS} passed, ${FAIL} failed ====="
[[ $FAIL -eq 0 ]] && exit 0 || exit 1

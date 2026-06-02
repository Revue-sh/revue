#!/usr/bin/env bash
# Test stop_pr_pipelines.sh selection logic without hitting the network.
# Uses two seams the script honours:
#   BB_PIPELINES_FIXTURE — read the pipeline-list JSON from this file instead of GET
#   BB_DRY_RUN=1         — print "WOULD STOP #<build> <uuid>" instead of POSTing
# Run: bash .claude/skills/bitbucket-merge-pr/scripts/tests/test_stop_pr_pipelines.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STOP="${SCRIPT_DIR}/stop_pr_pipelines.sh"

PASS=0
FAIL=0
_pass() { PASS=$((PASS+1)); echo "  ✅ $1"; }
_fail() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

FIX="$(mktemp)"
trap 'rm -f "$FIX"' EXIT

# Golden fixture — target/state shapes match a real Bitbucket pipelines
# response captured 2026-06-02 (GET /pipelines/?sort=-created_on). Verified
# facts baked in: PR pipelines carry the branch in `.target.source` and have
# `.target.ref_name == null` (this is why the source-branch fallback exists);
# `.target.pullrequest.id` is a JSON number; running state is "IN_PROGRESS".
# PR under test = 101, branch = feat/REVUE-1.
#   #11 IN_PROGRESS, our PR        -> stop
#   #12 PENDING, our PR            -> stop
#   #13 COMPLETED, our PR          -> skip (already finished)
#   #14 IN_PROGRESS, other PR 999  -> skip (not our PR)
#   #15 IN_PROGRESS, branch match  -> stop (ref pipeline on our source branch)
#   #16 IN_PROGRESS, other branch  -> skip
#   #17 IN_PROGRESS, PR pipeline whose id does NOT match but source branch does
#       -> stop (proves the source-branch fallback covers PR targets, whose
#          .target.ref_name is null)
cat > "$FIX" <<'JSON'
{ "values": [
  { "uuid": "{aaaa-11}", "build_number": 11, "state": {"name": "IN_PROGRESS"},
    "target": {"type": "pipeline_pullrequest_target", "ref_name": null, "source": "feat/REVUE-1", "destination": "main", "pullrequest": {"type": "pullrequest", "id": 101}} },
  { "uuid": "{bbbb-12}", "build_number": 12, "state": {"name": "PENDING"},
    "target": {"type": "pipeline_pullrequest_target", "ref_name": null, "source": "feat/REVUE-1", "destination": "main", "pullrequest": {"type": "pullrequest", "id": 101}} },
  { "uuid": "{cccc-13}", "build_number": 13, "state": {"name": "COMPLETED"},
    "target": {"type": "pipeline_pullrequest_target", "ref_name": null, "source": "feat/REVUE-1", "destination": "main", "pullrequest": {"type": "pullrequest", "id": 101}} },
  { "uuid": "{dddd-14}", "build_number": 14, "state": {"name": "IN_PROGRESS"},
    "target": {"type": "pipeline_pullrequest_target", "ref_name": null, "source": "feat/OTHER", "destination": "main", "pullrequest": {"type": "pullrequest", "id": 999}} },
  { "uuid": "{eeee-15}", "build_number": 15, "state": {"name": "IN_PROGRESS"},
    "target": {"type": "pipeline_ref_target", "ref_type": "branch", "ref_name": "feat/REVUE-1"} },
  { "uuid": "{ffff-16}", "build_number": 16, "state": {"name": "IN_PROGRESS"},
    "target": {"type": "pipeline_ref_target", "ref_type": "branch", "ref_name": "feat/UNRELATED"} },
  { "uuid": "{9999-17}", "build_number": 17, "state": {"name": "IN_PROGRESS"},
    "target": {"type": "pipeline_pullrequest_target", "ref_name": null, "source": "feat/REVUE-1", "destination": "main", "pullrequest": {"type": "pullrequest", "id": 777}} }
] }
JSON

# --- TC1: dry-run selects exactly the right pipelines ----------------------
echo "TC1: selects running pipelines for the PR and its source branch"
OUT="$(BB_DRY_RUN=1 BB_PIPELINES_FIXTURE="$FIX" bash "$STOP" 101 feat/REVUE-1 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && _pass "exit 0" || _fail "exit $RC (expected 0)"
echo "$OUT" | grep -q "#11" && _pass "stops #11 (in-progress PR pipeline)" || _fail "missed #11"
echo "$OUT" | grep -q "#12" && _pass "stops #12 (pending PR pipeline)"     || _fail "missed #12"
echo "$OUT" | grep -q "#15" && _pass "stops #15 (branch pipeline)"         || _fail "missed #15"
echo "$OUT" | grep -q "#17" && _pass "stops #17 (PR pipeline matched via source branch, not id)" || _fail "missed #17 — source-branch fallback broken for PR targets"
! echo "$OUT" | grep -q "#13" && _pass "skips #13 (completed)"             || _fail "wrongly stopped #13"
! echo "$OUT" | grep -q "#14" && _pass "skips #14 (other PR)"              || _fail "wrongly stopped #14"
! echo "$OUT" | grep -q "#16" && _pass "skips #16 (unrelated branch)"      || _fail "wrongly stopped #16"
echo "$OUT" | grep -qi "WOULD STOP" && _pass "dry-run does not POST"       || _fail "dry-run marker missing"

# --- TC2: nothing to stop is a clean exit 0, not an error -----------------
echo "TC2: no matching pipelines exits 0 with an informative message"
OUT="$(BB_DRY_RUN=1 BB_PIPELINES_FIXTURE="$FIX" bash "$STOP" 12345 feat/NONE 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && _pass "exit 0" || _fail "exit $RC (expected 0)"
echo "$OUT" | grep -qi "no .*pipeline" && _pass "reports nothing to stop" || _fail "no 'nothing to stop' message"
! echo "$OUT" | grep -qi "WOULD STOP" && _pass "stops nothing" || _fail "stopped something unexpectedly"

# --- TC3: branch arg is optional; PR-id match alone is enough -------------
echo "TC3: works with PR id only (no branch arg)"
OUT="$(BB_DRY_RUN=1 BB_PIPELINES_FIXTURE="$FIX" bash "$STOP" 101 2>&1)"
RC=$?
[[ $RC -eq 0 ]] && _pass "exit 0" || _fail "exit $RC (expected 0)"
echo "$OUT" | grep -q "#11" && _pass "still stops #11 via PR id"  || _fail "missed #11 without branch arg"
! echo "$OUT" | grep -q "#15" && _pass "no branch arg => no branch match" || _fail "matched branch pipeline without branch arg"
! echo "$OUT" | grep -q "#17" && _pass "no branch arg => no source-branch match for non-matching PR id" || _fail "matched #17 without branch arg"

# --- TC4: missing PR id is a usage error ----------------------------------
echo "TC4: missing PR id is rejected"
OUT="$(BB_DRY_RUN=1 BB_PIPELINES_FIXTURE="$FIX" bash "$STOP" 2>&1)"
RC=$?
[[ $RC -ne 0 ]] && _pass "exit non-zero" || _fail "exit 0 (expected usage error)"
echo "$OUT" | grep -qi "usage" && _pass "prints usage" || _fail "no usage message"

# --- TC5: non-numeric PR id is rejected (jq --argjson would choke) ---------
echo "TC5: non-numeric PR id is rejected"
OUT="$(BB_DRY_RUN=1 BB_PIPELINES_FIXTURE="$FIX" bash "$STOP" not-a-number feat/REVUE-1 2>&1)"
RC=$?
[[ $RC -ne 0 ]] && _pass "exit non-zero" || _fail "exit 0 (expected usage error)"
echo "$OUT" | grep -qi "numeric" && _pass "explains PR_ID must be numeric" || _fail "no numeric-validation message"
! echo "$OUT" | grep -qi "WOULD STOP" && _pass "stops nothing" || _fail "stopped something on bad input"

echo ""
echo "===== Results: ${PASS} passed, ${FAIL} failed ====="
[[ $FAIL -eq 0 ]] && exit 0 || exit 1

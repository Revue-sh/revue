#!/usr/bin/env bash
# Run the full Revue test suite across every test root in the repo.
#
# The tree was refactored away from a single src/revue/ package: tests now live
# in several disjoint roots, each with its own conftest.py. A single pytest
# invocation across all of them fails with ImportPathMismatchError (duplicate
# `tests` packages), so each suite is run as a SEPARATE invocation and the
# results are aggregated here.
#
# Interpreter + per-suite PYTHONPATH policy live in _common.sh.
#
# Output contract:
#   - All suites pass → per-suite summary lines + "ALL SUITES PASSED" (exit 0).
#   - Any suite fails  → the failing suites' last ~60 lines, a summary block,
#                        then a nonzero exit (the FIRST failing suite's code).
#   - A suite that collects zero tests (pytest rc 5) is a WARNING, not a
#     failure — an empty / not-yet-populated root must not fail the whole run.
set -euo pipefail

# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"

# Test roots, in run order. pythonpath_for() (in _common.sh) decides each
# suite's PYTHONPATH, so run_all.sh and run_tests.sh apply one identical policy.
SUITES=(
    "packaging/revue/tests"
    "packaging/revue-ci/tests"
    "packaging/revue_core/tests"
    "src/web/tests"
    "tests"
)

# Drift guard: a hardcoded SUITES list silently stops running a suite that gets
# moved/renamed, or never runs a newly-added one — a test runner reporting
# success for tests it skipped. Warn (don't fail) when a first-party `*/tests`
# dir under packaging/ or src/ holds test_*.py but no SUITES entry covers it.
_warn_unlisted_roots() {
    local d covered s
    while IFS= read -r d; do
        d="${d#./}"
        covered=0
        for s in "${SUITES[@]}"; do
            if [[ "$d" == "$s" || "$d" == "$s"/* || "$s" == "$d"/* ]]; then
                covered=1
                break
            fi
        done
        if [[ $covered -eq 0 ]]; then
            if find "$d" -maxdepth 2 -name 'test_*.py' -print -quit 2>/dev/null | grep -q .; then
                echo "WARNING: test directory '$d' has test_*.py but is not in run_all.sh's SUITES — it will NOT be run." >&2
            fi
        fi
    done < <(find packaging src -maxdepth 3 -type d -name tests \
                  -not -path '*/.venv/*' -not -path '*/venv/*' \
                  -not -path '*/node_modules/*' 2>/dev/null)
}
_warn_unlisted_roots

declare -a SUMMARIES=()
declare -a FAILED_SUITES=()
declare -a FAILED_OUTPUTS=()
overall_rc=0

for suite in "${SUITES[@]}"; do
    if [[ ! -d "$suite" ]]; then
        echo "WARNING: suite directory '$suite' does not exist — skipping." >&2
        SUMMARIES+=("$suite: SKIPPED (directory missing)")
        continue
    fi

    pp="$(pythonpath_for "$suite")"

    set +e
    if [[ -n "$pp" ]]; then
        output=$(PYTHONPATH="$pp" "$PY" -m pytest "$suite" -q --tb=short 2>&1)
    else
        output=$("$PY" -m pytest "$suite" -q --tb=short 2>&1)
    fi
    rc=$?
    set -e

    summary=$(printf '%s\n' "$output" | tail -1)

    # pytest rc 5 = "no tests collected": benign empty suite, not a failure.
    if [[ $rc -eq 5 ]]; then
        SUMMARIES+=("$suite: WARNING — no tests collected (pytest rc 5)")
        continue
    fi

    SUMMARIES+=("$suite: $summary")

    if [[ $rc -ne 0 ]]; then
        # Preserve the FIRST non-zero rc so a later test failure (rc 1) cannot
        # mask an earlier collection/internal error (rc 2/3/4).
        if [[ $overall_rc -eq 0 ]]; then
            overall_rc=$rc
        fi
        FAILED_SUITES+=("$suite")
        FAILED_OUTPUTS+=("$output")
    fi
done

if [[ $overall_rc -eq 0 ]]; then
    printf '%s\n' "${SUMMARIES[@]}"
    echo "ALL SUITES PASSED"
    exit 0
fi

# Failure path: show each failing suite's tail, then the aggregate summary.
for i in "${!FAILED_SUITES[@]}"; do
    echo "########## FAILED: ${FAILED_SUITES[$i]} ##########"
    printf '%s\n' "${FAILED_OUTPUTS[$i]}" | tail -60
    echo
done

echo "########## SUITE SUMMARY ##########"
printf '%s\n' "${SUMMARIES[@]}"
exit "$overall_rc"

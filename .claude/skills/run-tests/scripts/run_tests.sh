#!/usr/bin/env bash
# Run one or more specific tests (or a whole file) from the Revue test suite.
#
# Usage:
#   run_tests.sh <test_target> [<test_target> ...]
#
# <test_target> is a pytest node ID relative to the REPO ROOT, e.g.:
#   tests/test_resolve_cli.py
#   packaging/revue/tests/test_packaging.py::test_some_case
#   src/web/tests/test_dashboard.py::test_landing_page
#
# The tree was refactored away from a single src/revue/ package; tests now live
# in several roots (tests/, src/web/tests/, packaging/*/tests/). Pass the path
# as it sits relative to the repo root.
#
# Interpreter + per-suite PYTHONPATH policy live in _common.sh, shared with
# run_all.sh so the two scripts never disagree.
set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: run_tests.sh <test_target> [<test_target> ...]" >&2
    echo "Example: run_tests.sh tests/test_resolve_cli.py::test_main_invokes_service" >&2
    exit 1
fi

# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"

# Apply the same PYTHONPATH policy as run_all.sh: only root tests/ targets get
# src/ on the path; packaging/* and src/web targets must NOT (shadowing risk).
# Mixing roots in one invocation has no single correct path AND collapses
# pytest's rootdir to REPO_ROOT (pulling in the root conftest) — warn and run
# without src/ so packaging imports are never shadowed.
needs_src=0
other_root=0
for arg in "$@"; do
    if [[ -n "$(pythonpath_for "$arg")" ]]; then
        needs_src=1
    else
        other_root=1
    fi
done

if [[ $needs_src -eq 1 && $other_root -eq 1 ]]; then
    echo "WARNING: targets span multiple test roots; run root tests/ separately" >&2
    echo "         from packaging/web suites to avoid import-path surprises." >&2
fi

if [[ $needs_src -eq 1 && $other_root -eq 0 ]]; then
    PYTHONPATH="$REPO_ROOT/src" "$PY" -m pytest "$@" -v --tb=long 2>&1 | tail -30
else
    "$PY" -m pytest "$@" -v --tb=long 2>&1 | tail -30
fi

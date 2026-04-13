#!/usr/bin/env bash
# Run one or more specific tests (or a whole file) from the revue test suite.
#
# Usage:
#   run_tests.sh <test_target> [<test_target> ...]
#
# <test_target> is the pytest node ID relative to src/revue/tests/, e.g.:
#   comments/test_service.py
#   comments/test_service.py::test_collect_threads_uses_platform_for_store_lookup
#   core/test_pipeline.py::test_foo core/test_pipeline.py::test_bar
#
# The script prepends "revue/tests/" when the target does not already start with it.

set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: run_tests.sh <test_target> [<test_target> ...]" >&2
    echo "Example: run_tests.sh comments/test_service.py::test_collect_threads_uses_platform_for_store_lookup" >&2
    exit 1
fi

cd "$(dirname "$0")/../../../.." || exit 1   # repo root
cd src

targets=()
for arg in "$@"; do
    # Allow caller to pass bare paths (e.g. comments/test_service.py) or full
    # paths (revue/tests/comments/test_service.py) — normalise to the latter.
    if [[ "$arg" != revue/tests/* ]]; then
        arg="revue/tests/$arg"
    fi
    targets+=("$arg")
done

PYTHONPATH=$(pwd) python3 -m pytest "${targets[@]}" -v --tb=long 2>&1 | tail -30

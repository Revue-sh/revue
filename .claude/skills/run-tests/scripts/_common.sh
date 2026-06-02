#!/usr/bin/env bash
# Shared helpers for the run-tests skill scripts (run_all.sh, run_tests.sh).
#
# Sourced, not executed. Resolves the repo root and the virtualenv interpreter,
# and centralises the per-suite PYTHONPATH policy so the two scripts can never
# disagree about which suites need src/ on the path.
#
# Callers must `set -euo pipefail` before sourcing.

# --- Repo root -------------------------------------------------------------
# scripts/ -> run-tests/ -> skills/ -> .claude/ -> repo root (four levels up).
# A bare `cd ... && pwd` that fails would leave REPO_ROOT empty and surface a
# confusing error later; guard it explicitly here instead.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." 2>/dev/null && pwd)"
if [[ -z "${REPO_ROOT:-}" || ! -d "$REPO_ROOT/.claude" ]]; then
    echo "ERROR: could not resolve repo root from ${BASH_SOURCE[0]}" >&2
    exit 1
fi

# --- Interpreter -----------------------------------------------------------
# The repo virtualenv has all deps (jwt, pytest, ...). Bare `python3` on PATH
# is the wrong interpreter and lacks dependencies; never use it.
PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "ERROR: virtualenv interpreter not found at $PY" >&2
    echo "Create it and install deps before running the suite." >&2
    exit 1
fi

# --- PYTHONPATH policy -----------------------------------------------------
# Only the root `tests/` suite imports the historical top-level package layout
# and needs src/ on the path. Packaging suites (packaging/*/tests) are
# editable-installed and carry their own conftest/pyproject; putting src/ on
# their path risks shadowing top-level names (cli, db, web, reviews,
# AIReviewer) with the source tree, so they get NO extra PYTHONPATH.
#
# Echoes the PYTHONPATH a given suite/target path should run with (empty = none).
# Both run_all.sh and run_tests.sh route through this single rule.
pythonpath_for() {
    local target="$1"
    if [[ "$target" == "tests" || "$target" == tests/* ]]; then
        printf '%s' "$REPO_ROOT/src"
    fi
}

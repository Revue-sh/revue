#!/usr/bin/env bash
# Check: Nuitka CI flags must be present in build/build_nuitka.py
# Absence of --assume-yes-for-downloads causes CI to hang forever (REVUE-191).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "[check] nuitka-ci-flags..."
cd "$REPO_ROOT/src"
PYTHONPATH="$REPO_ROOT/src" python3 -m pytest \
  revue/tests/ci_guards/test_build_nuitka.py \
  -q --no-header 2>&1

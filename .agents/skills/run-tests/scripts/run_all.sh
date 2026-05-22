#!/usr/bin/env bash
# Run the full revue test suite.
# - All pass  → last 1 line (summary only, no dot noise).
# - Any fail  → last 60 lines (tracebacks + FAILED names + summary).
set -euo pipefail

cd "$(dirname "$0")/../../../.." || exit 1   # repo root

cd src
set +e
output=$(PYTHONPATH=$(pwd) python3 -m pytest revue/tests/ -q --tb=short 2>&1)
exit_code=$?
set -e

if [ $exit_code -eq 0 ]; then
    echo "$output" | tail -1
else
    echo "$output" | tail -60
    exit $exit_code
fi

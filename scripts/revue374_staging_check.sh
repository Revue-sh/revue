#!/usr/bin/env bash
# REVUE-374 — throwaway staging e2e check for the version manifest endpoint.
#
# Purpose: eyeball the full `revue install-skill` flow against the Fly STAGING
# manifest before a post-REVUE-372 wheel is published to PyPI.
#
# WHY --no-strict-version: install-skill's strict check compares
# manifest.current_version (PyPI latest) against the INSTALLED wheel's
# __version__. PyPI's current revue wheel still reports the REVUE-372 bug
# version ("0.1.0"), so the strict path fails by design until the next release.
# This script runs the strict path too, purely to SHOW that expected failure.
#
# DELETE THIS SCRIPT once AC5 is validated against a real published wheel
# (tracked by the REVUE-374 follow-up ticket).
set -euo pipefail

MANIFEST_URL="${1:-https://staging.revue.sh/skills/manifest.json}"

echo "== 1. Raw manifest =="
curl -fsS "$MANIFEST_URL" | python3 -m json.tool

echo
echo "== 2. install-skill (lenient: --no-strict-version) — expected: exit 0 =="
if revue install-skill --manifest-url "$MANIFEST_URL" --no-strict-version; then
  echo "  -> PASS (endpoint + schema + fetch path OK)"
else
  echo "  -> FAIL (rc=$?) — endpoint/schema/fetch problem, investigate" >&2
fi

echo
echo "== 3. install-skill (STRICT) — expected: exit 2 until post-REVUE-372 wheel is on PyPI =="
if revue install-skill --manifest-url "$MANIFEST_URL"; then
  echo "  -> STRICT PASSED — a corrected wheel is published; AC5 is now satisfiable."
else
  rc=$?
  echo "  -> STRICT failed (rc=$rc) as expected pre-release (manifest.current_version != installed wheel __version__)."
fi

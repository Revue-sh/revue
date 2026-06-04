#!/usr/bin/env bash
# End-to-end runner for scripts/install.sh in a disposable container.
#
# Builds a throwaway python:3.12-slim container, copies the repo's install.sh and
# the in-container harness into it, runs the harness (real pty + no-tty paths),
# and tears the container down. No host state is touched; the container is free to
# create /proj4, /new6, etc. Exit code mirrors the harness (non-zero on any
# failed check), so CI can gate on it.
#
# Usage:  packaging/revue/tests/e2e/run_install_e2e.sh
# Env:    E2E_IMAGE   override the base image (default python:3.12-slim)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# packaging/revue/tests/e2e -> repo root is four levels up.
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
INSTALL_SH="${REPO_ROOT}/scripts/install.sh"
HARNESS="${SCRIPT_DIR}/install_wizard_e2e.py"
IMAGE="${E2E_IMAGE:-python:3.12-slim}"
CONTAINER="revue-install-e2e-$$"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found — this e2e tool requires Docker." >&2
  exit 2
fi
if [[ ! -f "$INSTALL_SH" ]]; then
  echo "installer not found at ${INSTALL_SH}" >&2
  exit 2
fi

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$CONTAINER" "$IMAGE" sleep 300 >/dev/null
docker cp "$INSTALL_SH" "${CONTAINER}:/tmp/install.sh"
docker cp "$HARNESS" "${CONTAINER}:/tmp/install_wizard_e2e.py"
docker exec "$CONTAINER" python3 /tmp/install_wizard_e2e.py

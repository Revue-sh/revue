#!/usr/bin/env bash
# Render an epic progress recap (🟩/⬜ bar) for a Jira epic.
# Usage: recap.sh <KEY>   where KEY is an Epic or any ticket with a parent Epic.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: recap.sh <KEY>" >&2
    exit 2
fi

KEY="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIRA_SCRIPTS="$(cd "${SCRIPT_DIR}/../../jira-ticket/scripts" && pwd)"

# Step A: fetch the input issue; determine epic key
FETCH_OUT="$(bash "${JIRA_SCRIPTS}/jira_fetch.sh" "${KEY}")"
TYPE="$(printf '%s\n' "${FETCH_OUT}" | sed -n 's/^Type:[[:space:]]*//p' | head -1)"

if [ "${TYPE}" = "Epic" ]; then
    EPIC_KEY="${KEY}"
    EPIC_SUMMARY="$(printf '%s\n' "${FETCH_OUT}" | sed -n 's/^Summary:[[:space:]]*//p' | head -1)"
else
    EPIC_LINE="$(printf '%s\n' "${FETCH_OUT}" | grep '^Epic:' | head -1 || true)"
    if [ -z "${EPIC_LINE}" ]; then
        echo "error: ${KEY} has no parent epic" >&2
        exit 1
    fi
    EPIC_KEY="$(printf '%s\n' "${EPIC_LINE}" | sed -E 's/^Epic:[[:space:]]+([A-Z]+-[0-9]+).*/\1/')"
    EPIC_FETCH="$(bash "${JIRA_SCRIPTS}/jira_fetch.sh" "${EPIC_KEY}")"
    EPIC_SUMMARY="$(printf '%s\n' "${EPIC_FETCH}" | sed -n 's/^Summary:[[:space:]]*//p' | head -1)"
fi

# Step B: list children
CHILDREN="$(bash "${JIRA_SCRIPTS}/jira_search.sh" "parent = ${EPIC_KEY}" 100)"

# Step C+E: count + render via Python heredoc.
# Pass data as env vars (NOT stdin) so heredoc unambiguously supplies the script.
export EPIC_KEY EPIC_SUMMARY CHILDREN
python3 - <<'PY'
import os, re

epic_key = os.environ["EPIC_KEY"].strip()
epic_summary = os.environ["EPIC_SUMMARY"].strip()
children_raw = os.environ["CHILDREN"]

done = 0
active = 0
rejected = []

# jira_search.sh emits: KEY [Status] [Type] [Priority] EPIC summary
line_re = re.compile(r"^(\S+)\s+\[([^\]]+)\]")
for line in children_raw.splitlines():
    m = line_re.match(line)
    if not m:
        continue
    key, status = m.group(1), m.group(2).strip()
    if status in ("Done", "Closed"):
        done += 1
    elif status in ("Rejected", "Cancelled"):
        rejected.append(key)
    else:
        active += 1

denom = done + active
print(f"Epic: [{epic_key}] {epic_summary}")
if denom == 0:
    print("[" + "░" * 20 + "] 0/0 tickets (0%)")
else:
    pct = round(done / denom * 100)
    filled = int(done * 20 / denom)  # floor
    bar = "█" * filled + "░" * (20 - filled)
    print(f"[{bar}] {done}/{denom} tickets ({pct}%)")

if rejected:
    print(f"Excluded: {', '.join(rejected)} (rejected/cancelled)")
PY

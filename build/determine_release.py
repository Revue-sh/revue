#!/usr/bin/env python3
"""Determine the semantic release bump type from a conventional commit message.

Handles the project's commit format: type(scope)[TICKET]!: description

Outputs one of: major  minor  patch  none
"""
import re
import sys

# type(scope)[TICKET]!: description — all parts after type are optional
_COMMIT_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?(?:\[[^\]]*\])?(!)?\s*:")


def determine_bump(commit_msg: str) -> str:
    first_line = commit_msg.strip().split("\n")[0]

    if "BREAKING CHANGE" in commit_msg:
        return "major"

    m = _COMMIT_RE.match(first_line)
    if not m:
        return "none"

    commit_type, breaking = m.group(1), m.group(2)

    if breaking == "!":
        return "major"
    if commit_type == "feat":
        return "minor"
    if commit_type in ("fix", "perf", "refactor"):
        return "patch"
    return "none"


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else ""
    print(determine_bump(msg))

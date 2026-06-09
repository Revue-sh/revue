#!/usr/bin/env python3
"""Determine the semantic release bump type from a conventional commit message.

Handles the project's commit format: type(scope)[TICKET]!: description

Outputs one of: major  minor  patch  none

Optional positional args after the commit message are changed file paths.
If the message-based bump is "none" but any file is under packaging/, the
bump is upgraded to "patch" so wheel changes always reach PyPI.
"""
import re
import sys

# type(scope)[TICKET]!: description — all parts after type are optional
_COMMIT_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?(?:\[[^\]]*\])?(!)?\s*:")


def determine_bump(commit_msg: str, changed_files: list[str] | None = None) -> str:
    first_line = commit_msg.strip().split("\n")[0]

    if "BREAKING CHANGE" in commit_msg:
        return "major"

    m = _COMMIT_RE.match(first_line)
    if not m:
        msg_bump = "none"
    else:
        commit_type, breaking = m.group(1), m.group(2)
        if breaking == "!":
            return "major"
        if commit_type == "feat":
            msg_bump = "minor"
        elif commit_type in ("fix", "perf", "refactor"):
            msg_bump = "patch"
        else:
            msg_bump = "none"

    if msg_bump == "none" and changed_files:
        if any(f.startswith("packaging/") for f in changed_files):
            return "patch"

    return msg_bump


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sys.argv[2:] if len(sys.argv) > 2 else []
    print(determine_bump(msg, files))

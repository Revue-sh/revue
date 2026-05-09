"""Validate that a multi-line replacement span doesn't cross a code-block boundary.

Defensive guard against agents (and Nova) declaring a `replacement_line_count`
that extends the source span past the block being replaced — which produces
GitHub suggestion blocks anchored on unrelated code (the failure mode that
shipped as comment r3202849011 on PR #19).

Heuristic, not a parser: detects when the source span contains a line at
shallower-or-equal indent to `code_replacement[0]`, treats that as the start
of a sibling/parent block, and caps the span at the last non-blank line
before that boundary. Language-agnostic — works for anything that uses
indentation to denote block structure (Python, YAML, indented JS/TS bodies).
"""
from __future__ import annotations

import re

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def validate_replacement_span(
    diff: str,
    line_number: int,
    code_replacement: list[str] | None,
    declared_rlc: int,
) -> int:
    """Return a corrected `replacement_line_count` for the given span.

    Caps the declared count when the source span [line_number, line_number+rlc-1]
    in the diff crosses a code-block boundary. Returns `declared_rlc` unchanged
    when validation can't be performed (missing diff, line outside diff, etc.) —
    fail-open so we never produce a *smaller* span than the agent intended.
    """
    if declared_rlc <= 1 or not code_replacement:
        return declared_rlc
    if not diff:
        return declared_rlc

    new_lines = _extract_new_side_lines(diff)
    if line_number not in new_lines:
        return declared_rlc

    base_indent = _indent_of(code_replacement[0])

    boundary_offset: int | None = None
    for offset in range(1, declared_rlc):
        ln = line_number + offset
        content = new_lines.get(ln)
        if content is None:
            return declared_rlc
        if not content.strip():
            continue
        if _indent_of(content) <= base_indent:
            boundary_offset = offset
            break

    if boundary_offset is None:
        return declared_rlc

    last_block_offset = boundary_offset - 1
    while last_block_offset >= 1:
        content = new_lines.get(line_number + last_block_offset, "")
        if content.strip():
            break
        last_block_offset -= 1

    return max(1, last_block_offset + 1)


def is_anchor_coherent(
    diff: str,
    line_number: int,
    code_replacement: list[str] | None,
) -> bool:
    """Return False when ``code_replacement`` cannot drop in cleanly at ``line_number``.

    Heuristic: the indent of the first non-blank line of ``code_replacement`` must
    match the indent of the source line at ``line_number`` in the new-side of the
    diff. Mismatch means the agent anchored on "where the complaint lives" (e.g.
    the urlopen call inside a function) while the replacement starts somewhere
    else (e.g. a function definition at indent 0). Applying the suggestion would
    inject code at the wrong scope and break the file.

    Fail-open: when the diff is missing or doesn't contain ``line_number``, or
    when ``code_replacement`` is empty/None, returns True — never reject a
    suggestion we can't independently verify.
    """
    if not code_replacement:
        return True
    if not diff:
        return True

    new_lines = _extract_new_side_lines(diff)
    source_line = new_lines.get(line_number)
    if source_line is None:
        return True

    first_replacement_line = next(
        (line for line in code_replacement if line.strip()),
        None,
    )
    if first_replacement_line is None:
        return True

    return _indent_of(first_replacement_line) == _indent_of(source_line)


def _extract_new_side_lines(diff: str) -> dict[int, str]:
    """Map new-file line numbers to their content from a unified diff.

    Includes both `+` (added) and ` ` (context) lines, since both exist on
    the new side. Skips `-` (removed) lines since they don't exist in the
    new file. Returns empty dict for malformed diffs.
    """
    result: dict[int, str] = {}
    new_line_no: int | None = None
    for raw in diff.splitlines():
        if raw.startswith("@@"):
            match = _HUNK_HEADER_RE.match(raw)
            new_line_no = int(match.group(1)) if match else None
            continue
        if new_line_no is None:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            result[new_line_no] = raw[1:]
            new_line_no += 1
        elif raw.startswith(" "):
            result[new_line_no] = raw[1:]
            new_line_no += 1
        elif raw.startswith("-"):
            continue
    return result


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Matches unified diff hunk headers: @@ -old_start[,old_count] +new_start[,new_count] @@
# Groups: 1=old_start  2=old_count (None when absent → defaults to 1)
#          3=new_start  4=new_count (None when absent → defaults to 1)
# Special cases:  @@ -0,0 +1,N @@  (new file — old_count=0, all new lines are '+')
#                 @@ -1,N +0,0 @@  (deleted file — new_count=0, all old lines are '-')
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class PositionResult:
    file_path: str
    start_line: int | None
    end_line: int | None
    status: Literal["anchored", "context_line", "removed_line", "out_of_hunk", "absent"]
    reason: str


def _parse_diff(
    diff_snippet: str,
) -> tuple[set[int], set[int], set[int], list[tuple[int, int, int, int]]]:
    """Parse a unified diff snippet into line-number sets and hunk ranges.

    Returns:
        plus_new    — new-file line numbers that are '+' (added) lines
        context_new — new-file line numbers that are ' ' (context) lines
        minus_old   — old-file line numbers that are '-' (removed) lines
        hunks       — list of (old_start, old_count, new_start, new_count)
                      for each hunk header found; used for truncation inference
    """
    plus_new: set[int] = set()
    context_new: set[int] = set()
    minus_old: set[int] = set()
    hunks: list[tuple[int, int, int, int]] = []

    cur_old = cur_new = 0

    for line in diff_snippet.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            cur_old = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            cur_new = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            hunks.append((cur_old, old_count, cur_new, new_count))
            continue

        if line.startswith(("+++", "---", "diff --git", "index ", "\\ ")):
            continue

        if cur_old == 0 and cur_new == 0:
            continue  # Before the first hunk header

        if line.startswith("+"):
            plus_new.add(cur_new)
            cur_new += 1
        elif line.startswith("-"):
            minus_old.add(cur_old)
            cur_old += 1
        else:  # context (space-prefixed or blank)
            context_new.add(cur_new)
            cur_old += 1
            cur_new += 1

    return plus_new, context_new, minus_old, hunks


def calculate(
    diff_snippet: str,
    reported_line: int,
    file_path: str,
    replacement_line_count: int = 1,
) -> PositionResult:
    """Resolve reported_line within diff_snippet to a platform-agnostic PositionResult.

    Lookup order (first match wins):
      1. reported_line in plus_new    → anchored       ('+' line in new file)
      2. reported_line in context_new → context_line   (' ' line, not anchorable)
      3. reported_line in minus_old   → removed_line   ('-' line, old file only)
      4. otherwise                    → out_of_hunk

    Callers use a PositionAdapter to produce platform-specific api_params from the result.
    """
    plus_new, context_new, minus_old, hunks = _parse_diff(diff_snippet)

    if reported_line in plus_new:
        return PositionResult(
            file_path=file_path,
            start_line=reported_line,
            end_line=reported_line + replacement_line_count - 1,
            status="anchored",
            reason=f"line {reported_line} is a '+' line in the diff",
        )

    if reported_line in context_new:
        return PositionResult(
            file_path=file_path,
            start_line=None,
            end_line=None,
            status="context_line",
            reason=f"line {reported_line} is a context (' ') line — not anchorable",
        )

    if reported_line in minus_old:
        return PositionResult(
            file_path=file_path,
            start_line=None,
            end_line=None,
            status="removed_line",
            reason=f"line {reported_line} is a removed ('-') line in the old file",
        )

    # Truncation fallback: if the snippet was cut short before reaching reported_line,
    # infer from hunk header metadata alone.
    # A pure-addition hunk (@@ -0,0 +C,N @@, old_count=0) contains only '+' lines —
    # every new-file line in [new_start, new_start+new_count-1] is anchored even if
    # the platform API truncated the hunk body before that line.
    for old_start, old_count, new_start, new_count in hunks:
        if old_count == 0 and new_start <= reported_line <= new_start + new_count - 1:
            return PositionResult(
                file_path=file_path,
                start_line=reported_line,
                end_line=reported_line + replacement_line_count - 1,
                status="anchored",
                reason=(
                    f"line {reported_line} inferred as '+' from pure-addition hunk "
                    f"@@ -{old_start},{old_count} +{new_start},{new_count} @@ "
                    f"(snippet truncated before this line)"
                ),
            )

    return PositionResult(
        file_path=file_path,
        start_line=None,
        end_line=None,
        status="out_of_hunk",
        reason=f"line {reported_line} does not appear in any diff hunk",
    )

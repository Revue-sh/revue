"""Hard diff limit guard (PRD: default 2000 lines, configurable, non-blocking)."""
from __future__ import annotations

from dataclasses import dataclass
from .models import FileChange


@dataclass
class DiffLimitResult:
    exceeded: bool
    total_lines: int
    limit: int
    largest_files: list[tuple[str, int]]
    suggestion: str = ""

    @property
    def exit_as_warning(self) -> bool:
        """True when limit exceeded — exit code 0 (non-blocking, per PRD)."""
        return self.exceeded


def check_diff_limit(changes: list[FileChange], limit: int = 2000) -> DiffLimitResult:
    """
    Check if total lines changed exceeds limit.

    - total_lines = sum of (additions + deletions) for all files
    - If exceeded: populate suggestion with top-5 largest files
    - Non-blocking: exceeded is a warning not an error (PRD requirement)
    - Never raises
    """
    file_lines = [(fc.file_path, fc.additions + fc.deletions) for fc in changes]
    total = sum(lines for _, lines in file_lines)
    exceeded = total > limit
    sorted_files = sorted(file_lines, key=lambda x: x[1], reverse=True)[:5]
    suggestion = ""
    if exceeded:
        lines_str = "\n".join(
            f"  - {path}: {lines} lines changed" for path, lines in sorted_files
        )
        suggestion = (
            f"Diff too large ({total} lines, limit {limit}). "
            f"Consider breaking into smaller PRs.\nLargest files:\n{lines_str}"
        )
    return DiffLimitResult(
        exceeded=exceeded,
        total_lines=total,
        limit=limit,
        largest_files=sorted_files,
        suggestion=suggestion,
    )

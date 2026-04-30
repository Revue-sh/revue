"""DiffPositionResolver — 3-tier line number snapping for REVUE-201.

Resolves agent-reported line numbers to accurate positions by:
1. Tier 1: Exact match in diff hunks
2. Tier 2: Nearest hunk line when outside diff bounds
3. Tier 3: File read fallback when repo_path provided (out-of-diff case)
"""

import functools
import re
from pathlib import Path


class DiffPositionResolver:
    """Snaps agent-reported line numbers to valid positions in a diff or file."""

    _DIFF_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    @staticmethod
    def snap(
        reported_line: int,
        diff_content: str,
        repo_path: str | None = None,
        file_path: str | None = None,
    ) -> int:
        """Snap reported_line to a valid position using 3-tier strategy.

        Args:
            reported_line: Agent-reported line number (1-indexed).
            diff_content: Unified diff bytes as string.
            repo_path: Root of cloned repo (for Tier 3 fallback). None disables Tier 3.
            file_path: Relative path within repo (used only if repo_path provided).

        Returns:
            A line number that exists in the diff (Tier 1/2) or file (Tier 3).
        """
        # Extract all (old_line, new_line) pairs from diff
        pairs = DiffPositionResolver._map_diff_lines(diff_content)

        if not pairs:
            # Empty diff — line 1 is the safest anchor
            return 1

        # Tier 1: Exact match in new_line?
        for _, new_line in pairs:
            if new_line == reported_line:
                return reported_line

        # Tier 2: Snap to nearest new_line in diff
        nearest = min(pairs, key=lambda p: abs(p[1] - reported_line))
        nearest_line = nearest[1]

        # Tier 3: If file access is available, clamp reported_line to valid file range
        if repo_path and file_path:
            # Reject null bytes before constructing a Path (Path() accepts them silently)
            if "\x00" in file_path:
                return nearest_line
            fp = Path(file_path)
            # Reject absolute paths and '..' traversal components
            if fp.is_absolute() or ".." in fp.parts:
                return nearest_line
            full_path = Path(repo_path) / fp
            # Use relative_to() — raises ValueError if full_path escapes repo root
            # (handles symlinks and case-insensitive filesystem edge cases)
            try:
                resolved_full = full_path.resolve()
                resolved_repo = Path(repo_path).resolve()
                resolved_full.relative_to(resolved_repo)
            except Exception:
                return nearest_line

            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="ignore")
                    num_lines = len(content.splitlines())
                    if num_lines > 0:
                        return max(1, min(reported_line, num_lines))
                except Exception:
                    pass  # Fall back to Tier 2

        return nearest_line

    @staticmethod
    def line_in_diff(line: int, diff_content: str) -> bool:
        """Return True if *line* appears as a new_line in the diff."""
        pairs = DiffPositionResolver._map_diff_lines(diff_content)
        return any(new_line == line for _, new_line in pairs)

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _map_diff_lines(diff_content: str) -> tuple[tuple[int, int], ...]:
        """Return all (old_line, new_line) pairs from a unified diff.

        Added lines (+) have old_line=0. Removed lines are excluded.
        Returns empty tuple if diff is empty or malformed.
        The returned tuple is immutable — safe to share across lru_cache callers.
        """
        result: list[tuple[int, int]] = []
        cur_old = cur_new = 0

        for raw in diff_content.splitlines():
            m = DiffPositionResolver._DIFF_HUNK_RE.match(raw)
            if m:
                cur_old = int(m.group(1))
                cur_new = int(m.group(2))
                continue

            if raw.startswith(("+++", "---", "diff --git")):
                continue

            # Skip "\ No newline at end of file" markers
            if raw.startswith("\\ "):
                continue

            if cur_new == 0 and cur_old == 0:
                continue  # Before first hunk

            if raw.startswith("+"):
                result.append((0, cur_new))
                cur_new += 1
            elif raw.startswith("-"):
                cur_old += 1
            else:  # Context line (or blank)
                result.append((cur_old, cur_new))
                cur_old += 1
                cur_new += 1

        return tuple(result)

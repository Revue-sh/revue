#!/usr/bin/env python3
"""Diff ingestion — parse raw VCS diffs into FileChange objects."""

import re
from fnmatch import fnmatch
from pathlib import PurePosixPath

from revue.core.models import FileChange

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".rb": "ruby",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".cs": "csharp", ".cpp": "cpp", ".c": "c",
    ".h": "c", ".hpp": "cpp", ".php": "php", ".scala": "scala",
    ".sh": "shell", ".bash": "shell", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".md": "markdown", ".html": "html", ".css": "css",
}

_DIFF_HEADER_RE = re.compile(r"^diff --git a/.+ b/.+$", re.MULTILINE)
_BINARY_RE = re.compile(r"^Binary files .+ and .+ differ$", re.MULTILINE)


def detect_language(file_path: str) -> str:
    """Return language string from file extension, or 'unknown'."""
    ext = PurePosixPath(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext, "unknown")


def _strip_ab_prefix(path: str) -> str:
    """Strip leading 'a/' or 'b/' prefix from a diff path."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_diff(raw_diff: str) -> list[FileChange]:
    """Parse a unified diff string into FileChange objects.

    Handles standard unified diff format (git diff output), new files,
    deleted files, binary files, and multiple files in one diff string.
    Robust to malformed input — skips unparseable hunks, never raises.
    """
    if not raw_diff or not raw_diff.strip():
        return []

    changes: list[FileChange] = []

    # Split on "diff --git" boundaries
    split_points = [m.start() for m in _DIFF_HEADER_RE.finditer(raw_diff)]
    if not split_points:
        return []

    file_diffs: list[str] = []
    for i, start in enumerate(split_points):
        end = split_points[i + 1] if i + 1 < len(split_points) else len(raw_diff)
        file_diffs.append(raw_diff[start:end])

    for section in file_diffs:
        try:
            change = _parse_single_file_diff(section)
            if change is not None:
                changes.append(change)
        except Exception:
            # Skip unparseable sections
            continue

    return changes


def _parse_single_file_diff(section: str) -> FileChange | None:
    """Parse a single file's diff section into a FileChange."""
    lines = section.split("\n")
    if not lines:
        return None

    # Check for binary file
    if _BINARY_RE.search(section):
        file_path = _extract_file_path_from_header(lines[0])
        return FileChange(
            file_path=file_path,
            change_type="binary",
            additions=0,
            deletions=0,
            diff="[binary]",
            language=detect_language(file_path),
        )

    # Extract old/new paths from --- / +++ lines
    old_path: str | None = None
    new_path: str | None = None
    hunk_start_idx: int | None = None

    for i, line in enumerate(lines):
        if line.startswith("--- "):
            old_path = line[4:].strip()
        elif line.startswith("+++ "):
            new_path = line[4:].strip()
        elif line.startswith("@@"):
            hunk_start_idx = i
            break

    # Determine file path and change type
    if new_path is None and old_path is None:
        # Fallback: extract from diff --git header
        file_path = _extract_file_path_from_header(lines[0])
        change_type = "modified"
    elif new_path == "/dev/null":
        file_path = _strip_ab_prefix(old_path or "")
        change_type = "deleted"
    elif old_path == "/dev/null":
        file_path = _strip_ab_prefix(new_path or "")
        change_type = "added"
    else:
        file_path = _strip_ab_prefix(new_path or "")
        change_type = "modified"

    # Extract hunk text and count additions/deletions
    additions = 0
    deletions = 0
    diff_lines: list[str] = []

    if hunk_start_idx is not None:
        diff_lines = lines[hunk_start_idx:]
        for line in diff_lines:
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

    diff_text = "\n".join(diff_lines)

    return FileChange(
        file_path=file_path,
        change_type=change_type,
        additions=additions,
        deletions=deletions,
        diff=diff_text,
        language=detect_language(file_path),
    )


def _extract_file_path_from_header(header_line: str) -> str:
    """Extract file path from 'diff --git a/path b/path' header."""
    parts = header_line.split(" b/", 1)
    if len(parts) == 2:
        return parts[1].strip()
    # Fallback
    parts = header_line.split()
    if len(parts) >= 4:
        return _strip_ab_prefix(parts[-1])
    return "unknown"


def parse_diff_file(path: str) -> list[FileChange]:
    """Read a .diff file from disk and parse it."""
    with open(path, encoding="utf-8") as f:
        return parse_diff(f.read())


def filter_changes(
    changes: list[FileChange],
    ignore_patterns: list[str],
    max_lines_changed: int = 2000,
) -> tuple[list[FileChange], list[FileChange]]:
    """Return (included, excluded) tuples.

    Excluded if: matches any ignore_pattern (fnmatch) OR
    lines_changed > max_lines_changed.
    """
    included: list[FileChange] = []
    excluded: list[FileChange] = []

    for change in changes:
        lines_changed = change.additions + change.deletions
        # Check ignore patterns against the full path and the basename
        basename = PurePosixPath(change.file_path).name
        matched = any(
            fnmatch(change.file_path, pat) or fnmatch(basename, pat)
            for pat in ignore_patterns
        )
        if matched or lines_changed > max_lines_changed:
            excluded.append(change)
        else:
            included.append(change)

    return included, excluded

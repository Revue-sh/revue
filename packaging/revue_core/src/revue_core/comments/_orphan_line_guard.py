"""Deterministic backstop for block-completeness on Vex's ``apply`` verdict.

REVUE-249 / ADR §D4. Vex's prompt-level block-completeness guidance (§D1) is
the soft semantic layer; this post-processor is the hard layer that cannot
regress under a model swap.

The guard runs *after* :class:`VexVerifyPostProcessor` in the consolidator's
post-processor chain. For every finding that still carries a multi-line
``code_replacement``, the guard inspects the line immediately below the
proposed replacement range:

  * if that next non-blank line is at or deeper than the outermost indent in
    the range, the block continues past the patch — applying it would orphan
    the trailing lines, so the guard downgrades the finding to a prose-only
    suggestion (mirroring ``drop_cr_keep_prose``);
  * if that line is strictly outdented (or end-of-file is reached), the
    block terminated cleanly inside the range and the finding passes through.

Indent comparison is the only structural signal — no per-language AST. The
ADR's "Out of scope" section forbids tree-sitter / language-specific tooling
here, so the guard treats every file as a sequence of indent-bearing lines.
Leading whitespace is counted as raw characters (tabs as 1 char each, no
expansion) so the comparison stays language-agnostic.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from .models import ConsolidatedFinding
from ..core.logging_channels import Log
from ..core.tools import ReadFileTool


class OrphanLineGuardPostProcessor:
    """FindingPostProcessor that drops ``code_replacement`` when the proposed
    range stops short of the natural block terminator.

    Constructor mirrors :class:`VexVerifyPostProcessor`: takes ``repo_root``
    and the PR's ``diff_by_file`` so the underlying :class:`ReadFileTool`
    shares the same sandbox allowlist Vex uses.

    Observability (REVUE-249 AC7): every downgrade increments
    :attr:`guard_downgrade`, a read-only counter exposed separately from
    Vex's ``verdict_counts`` so the LLM-vs-guard contribution is visible to
    log greps.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        diff_by_file: dict[str, str],
    ) -> None:
        self._read_tool = ReadFileTool(
            repo_root=repo_root,
            allowed_paths=set(diff_by_file.keys()),
        )
        self._counters_lock = threading.Lock()
        self._guard_downgrade: int = 0
        # File-content cache scoped to a single review run. The guard is
        # instantiated per pipeline invocation, so staleness is impossible —
        # no concurrent editor touches the file between findings. The cache
        # stores the pre-split lines list (the splitlines call is itself
        # O(N) and would otherwise repeat per finding). A ``None`` value is
        # the negative-cache sentinel for files whose read failed — keeping
        # the failure cached prevents duplicate INFO logs and redundant
        # syscalls for the rest of the run.
        self._content_cache_lock = threading.Lock()
        self._content_cache: dict[str, list[str] | None] = {}

    @property
    def guard_downgrade(self) -> int:
        with self._counters_lock:
            return self._guard_downgrade

    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None:
        # Prose-only finding — nothing to inspect; the guard's contract is to
        # police destructive code_replacement spans, not to second-guess Vex
        # on rejections.
        if finding.code_replacement is None:
            return finding
        if finding.replacement_line_count <= 1:
            # Single-line replacement — no block to under-reach.
            return finding

        lines = self._load_file_lines(finding)
        if lines is None:
            return finding

        start_line = finding.line_number
        end_line = start_line + finding.replacement_line_count - 1
        should_downgrade, outermost_indent, trailing_indent = _should_downgrade(
            lines,
            start_line=start_line,
            end_line=end_line,
        )
        if not should_downgrade:
            return finding

        with self._counters_lock:
            self._guard_downgrade += 1
        Log.nova.warning(
            "[orphan-guard-downgrade] %s:%d trailing_indent=%d outermost_indent=%d",
            finding.file_path,
            end_line + 1,
            trailing_indent,
            outermost_indent,
        )
        return replace(
            finding,
            code_replacement=None,
            replacement_line_count=1,
            snippet="",
        )

    def _load_file_lines(self, finding: ConsolidatedFinding) -> list[str] | None:
        """Return the cached line list for ``finding.file_path``, or ``None``
        if the file's read previously failed (or fails on this first attempt).

        The lock is held across the cache check, the filesystem read, and the
        cache write — releasing it between check and write would race two
        concurrent first-touches on the same file and break the documented
        one-read-per-file contract. The read itself is I/O-bound, but the
        lock window is bounded by file size and the guard is short-lived
        (one pipeline invocation), so the serialisation cost is acceptable.

        Read errors are cached as ``None`` (negative cache) so a second
        finding on the same failing file skips the redundant syscall and
        does not emit a duplicate INFO log.
        """
        file_path = finding.file_path
        with self._content_cache_lock:
            if file_path in self._content_cache:
                return self._content_cache[file_path]

            read_result = self._read_tool.execute(path=file_path)
            if read_result.is_error:
                # AC10 log shape: ``[orphan-guard-failure] read_error file:line:
                # <error>`` — the line number lets dogfood greps tie the
                # failure back to the finding that triggered it.
                Log.nova.info(
                    "[orphan-guard-failure] read_error %s:%d: %s — keeping finding as-is.",
                    file_path,
                    finding.line_number,
                    read_result.content,
                )
                self._content_cache[file_path] = None
                return None

            lines = read_result.content.splitlines()
            self._content_cache[file_path] = lines
            return lines


def _leading_indent(line: str) -> int:
    """Count leading whitespace characters. Language-agnostic — tabs are
    counted as 1 char (no expansion), so a file mixing tabs and spaces is
    compared on raw lexical indent. This matches how diff parsers treat
    indent and avoids guessing tab width.
    """
    return len(line) - len(line.lstrip())


def _should_downgrade(
    lines: list[str],
    *,
    start_line: int,
    end_line: int,
) -> tuple[bool, int, int]:
    """Decide whether the replacement range under-reaches its block.

    Returns ``(should_downgrade, outermost_indent, trailing_indent)``. The
    second and third entries are only meaningful when the first is ``True`` —
    they're surfaced so the WARN log can show the comparison that triggered
    the downgrade.

    Algorithm (ADR §D4, indent-only):
      1. Compute the minimum leading-indent across the range, skipping blank
         lines. This is the indent of the *outermost* in-range statement —
         the level at which the patched block must close to terminate cleanly.
      2. Probe forward from end_line + 1, skipping blanks. If end-of-file is
         reached, the block terminated cleanly — accept.
      3. If the first non-blank trailing line has indent >= outermost_indent,
         the block continues into orphan territory — downgrade.
    """
    total = len(lines)

    range_indents: list[int] = []
    for line_no in range(start_line, end_line + 1):
        if line_no < 1 or line_no > total:
            continue
        line = lines[line_no - 1]
        if line.strip():
            range_indents.append(_leading_indent(line))
    if not range_indents:
        # Pathological — replacement range is entirely blank lines. Nothing
        # to compare against; accept rather than downgrade on noise.
        return False, 0, 0
    outermost_indent = min(range_indents)

    probe = end_line + 1
    while probe <= total and not lines[probe - 1].strip():
        probe += 1
    if probe > total:
        # End-of-file reached — the block naturally terminated.
        return False, outermost_indent, 0
    trailing_indent = _leading_indent(lines[probe - 1])
    return trailing_indent >= outermost_indent, outermost_indent, trailing_indent

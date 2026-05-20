"""Sandboxed file-read tool that Nova invokes during synthesis.

Nova uses this to load HEAD content for a PR-touched file before producing a
``code_replacement`` — without it, Nova guesses span boundaries from prose and
can produce single-line anchors with multi-line replacements (the destructive
pattern that REVUE-239 tracks).

Sandbox rules (MVP):
  - The requested path must be one of the PR's touched files (``allowed_paths``).
  - After ``Path.resolve()``, the file must still be under ``repo_root`` — symlink
    escapes and ``..`` traversal are rejected.
  - Reads are capped at ``max_lines`` and ``max_bytes`` so a 50MB log can't blow
    up Nova's context window.

Failure mode: every error returns a :class:`ToolResult` with ``is_error=True``
and a human-readable explanation that Nova can quote back in a prose-only
suggestion. The tool never raises into the tool-use loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a single tool invocation. ``is_error=True`` reports back to Nova
    via the tool_result block — Nova sees the message and adapts its output."""

    content: str
    is_error: bool = False


class ReadFileTool:
    """Sandboxed reader for files touched by the current PR."""

    # REVUE-243: tightened from 5_000 / 200_000 after PR #26 / 2026-05-13 local
    # dogfood blew 3/4 reviewers past the 200K context window. A first read
    # previously carried up to ~50K tokens (25% of the budget); 64 KiB / ~16K
    # tokens leaves room for the diff, system prompt, and 2-3 further reads
    # before the cumulative-result cap (AC3) forces graceful finalize.
    _DEFAULT_MAX_LINES = 1_500
    _DEFAULT_MAX_BYTES = 65_536

    def __init__(
        self,
        repo_root: Path,
        allowed_paths: set[str],
        max_lines: int = _DEFAULT_MAX_LINES,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._allowed_paths = allowed_paths
        self._max_lines = max_lines
        self._max_bytes = max_bytes

    @staticmethod
    def tool_definition() -> dict:
        """JSON schema consumed by Anthropic's ``tools=[...]`` parameter."""
        return {
            "name": "read_file",
            "description": (
                "Read the full contents of a file at the PR's HEAD revision. "
                "Use this before producing code_replacement so you can verify "
                "the span you propose to replace and the indentation of "
                "surrounding code. Only files touched by this PR are readable."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Repo-relative path of the file to read. Must match "
                            "one of the files touched by this PR."
                        ),
                    },
                },
                "required": ["path"],
            },
        }

    def execute(self, *, path: str) -> ToolResult:
        if path not in self._allowed_paths:
            return ToolResult(
                content=(
                    f"Error: '{path}' is not in this PR's file set. "
                    f"read_file can only access files touched by the PR. "
                    f"If you need wider context, omit code_replacement and "
                    f"explain the limitation in your prose suggestion."
                ),
                is_error=True,
            )

        full_path = (self._repo_root / path).resolve()
        try:
            resolved_relpath = full_path.relative_to(self._repo_root)
        except ValueError:
            return ToolResult(
                content=(
                    f"Error: '{path}' resolves outside the repo root. "
                    f"Path traversal is not permitted."
                ),
                is_error=True,
            )

        # Defence-in-depth: a malicious commit could ship a symlink whose name
        # appears in allowed_paths but whose target is a different (un-touched)
        # file inside the repo. The first check accepted the symlink by name;
        # this second check rejects it by destination, so Nova/Vex can't be
        # tricked into forwarding the content of files the PR never touched.
        if str(resolved_relpath) not in self._allowed_paths:
            return ToolResult(
                content=(
                    f"Error: '{path}' resolves to '{resolved_relpath}' which is "
                    f"not in this PR's file set. Symlink-redirected reads are "
                    f"not permitted."
                ),
                is_error=True,
            )

        if not full_path.exists():
            return ToolResult(
                content=f"Error: file '{path}' does not exist at HEAD.",
                is_error=True,
            )

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                content=f"Error: file '{path}' is not valid UTF-8 text.",
                is_error=True,
            )

        size_bytes = len(content.encode("utf-8"))
        if size_bytes > self._max_bytes:
            return ToolResult(
                content=(
                    f"Error: file '{path}' is {size_bytes} bytes "
                    f"(> {self._max_bytes} byte cap). The file is too large for "
                    f"full-context analysis. Omit code_replacement and write a "
                    f"prose suggestion advising the developer to apply the fix "
                    f"in their development environment instead of via PR "
                    f"suggestion."
                ),
                is_error=True,
            )

        line_count = content.count("\n") + (0 if content.endswith("\n") else 1)
        if line_count > self._max_lines:
            return ToolResult(
                content=(
                    f"Error: file '{path}' has {line_count} lines "
                    f"(> {self._max_lines} line cap). The file is too large for "
                    f"full-context analysis. Omit code_replacement and write a "
                    f"prose suggestion advising the developer to apply the fix "
                    f"in their development environment instead of via PR "
                    f"suggestion."
                ),
                is_error=True,
            )

        return ToolResult(content=content, is_error=False)

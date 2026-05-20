"""Sandboxed line-range reader — returns ±N lines around a known line number.

REVUE-243: complementary to ``ReadFileTool``. Reviewer agents typically know
the exact line they want to verify (it's in the diff hunk header) so they
should not have to load 1500 lines of context to inspect 20 of them. This
tool returns a tight window, which keeps cumulative tool-result size small
enough for multiple iterations to fit under the 200K window.

Sandbox parity with ``ReadFileTool``: ``allowed_paths`` gating, repo-root
containment after ``Path.resolve()``, symlink-destination revalidation.

Failure mode mirrors ``ReadFileTool``: every error returns ``ToolResult``
with ``is_error=True`` and a human-readable explanation. The tool never
raises into the tool-use loop.
"""
from __future__ import annotations

from pathlib import Path

from .read_file import ToolResult


class ReadLinesTool:
    """Sandboxed reader for a specific line window inside a PR-touched file."""

    # Defensive cap on the requested context window. An agent that asks for
    # 5000 lines of context defeats the purpose of this tool — clamp so a
    # single call can't blow the cumulative budget on its own.
    _MAX_CONTEXT = 500

    def __init__(
        self,
        repo_root: Path,
        allowed_paths: set[str],
        max_context: int = _MAX_CONTEXT,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._allowed_paths = allowed_paths
        self._max_context = max_context

    @staticmethod
    def tool_definition() -> dict:
        """JSON schema consumed by Anthropic's ``tools=[...]`` parameter."""
        return {
            "name": "read_lines",
            "description": (
                "Read a window of lines around a specific line number in a "
                "PR-touched file. Use this when you know the line you want to "
                "verify (it appears in the diff hunk header) — far cheaper "
                "than read_file because the tool returns only ~2 * context "
                "lines, not the whole file."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Repo-relative path. Must match a file touched by "
                            "this PR."
                        ),
                    },
                    "around_line": {
                        "type": "integer",
                        "description": (
                            "1-indexed line number to centre the window on. "
                            "Use the line number from the diff hunk."
                        ),
                    },
                    "context": {
                        "type": "integer",
                        "description": (
                            "Number of lines to include before and after "
                            "around_line. Default 50. Capped at 500."
                        ),
                        "default": 50,
                    },
                },
                "required": ["path", "around_line"],
            },
        }

    def execute(self, *, path: str, around_line: int, context: int = 50) -> ToolResult:
        # Sandbox check 1 — path must be in the PR's touched files.
        if path not in self._allowed_paths:
            return ToolResult(
                content=(
                    f"Error: '{path}' is not in this PR's file set. "
                    f"read_lines can only access files touched by the PR."
                ),
                is_error=True,
            )

        full_path = (self._repo_root / path).resolve()
        # Sandbox check 2 — resolved path must stay under repo_root.
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

        # Sandbox check 3 — symlink target must also be in the allowed set.
        if str(resolved_relpath) not in self._allowed_paths:
            return ToolResult(
                content=(
                    f"Error: '{path}' resolves to '{resolved_relpath}' which "
                    f"is not in this PR's file set. Symlink-redirected reads "
                    f"are not permitted."
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

        # Clamp context window so a single call can't dominate the loop budget.
        effective_context = max(0, min(context, self._max_context))
        lines = content.splitlines()
        total = len(lines)

        # Clamp 1-indexed window bounds. Empty / out-of-range targets still
        # produce a usable slice rather than an error — the model can read
        # what it got and self-correct on the next iteration.
        start_idx = max(0, around_line - 1 - effective_context)
        end_idx = min(total, around_line + effective_context)
        window = lines[start_idx:end_idx]

        return ToolResult(content="\n".join(window), is_error=False)

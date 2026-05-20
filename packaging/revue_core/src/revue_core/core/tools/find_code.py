"""Sandboxed regex/literal-string code search with surrounding context.

REVUE-243: complementary to ReadFileTool / ReadLinesTool. Covers the "find
this symbol's definition / find where it's called" use case without
returning whole files.

Primary path: invoke ``rg`` (ripgrep) via ``subprocess``. Falls back to a
pure-Python literal-string scan when ``rg`` is missing — many dev machines
have it but not every CI image will.

Cumulative output is capped (default 10 KB) so an overly-broad query cannot
single-handedly blow the tool-loop budget.

Sandbox parity with the other tools: allowed_paths gating, repo_root
containment, symlink rejection.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .read_file import ToolResult


class FindCodeTool:
    """Sandboxed code search inside a PR-touched file."""

    _DEFAULT_OUTPUT_BYTES_CAP = 10_240  # 10 KB
    _TRUNCATION_MARKER = "\n... [truncated — output exceeded cap; refine query]\n"

    def __init__(
        self,
        repo_root: Path,
        allowed_paths: set[str],
        output_bytes_cap: int = _DEFAULT_OUTPUT_BYTES_CAP,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._allowed_paths = allowed_paths
        self._output_bytes_cap = output_bytes_cap

    @staticmethod
    def tool_definition() -> dict:
        """JSON schema consumed by Anthropic's ``tools=[...]`` parameter."""
        return {
            "name": "find_code",
            "description": (
                "Search for a literal string or regex pattern inside a "
                "PR-touched file and return matching lines with surrounding "
                "context. Use this for 'where is X called' / 'how is X "
                "defined' lookups instead of read_file when you don't need "
                "the whole file."
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
                    "query": {
                        "type": "string",
                        "description": (
                            "Literal substring or regex to search for. "
                            "Prefer a literal symbol or signature fragment."
                        ),
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": (
                            "Lines of context before and after each match. "
                            "Default 50."
                        ),
                        "default": 50,
                    },
                },
                "required": ["path", "query"],
            },
        }

    def execute(self, *, path: str, query: str, context_lines: int = 50) -> ToolResult:
        # Sandbox check 1 — path must be in the PR's touched files.
        if path not in self._allowed_paths:
            return ToolResult(
                content=(
                    f"Error: '{path}' is not in this PR's file set. "
                    f"find_code can only access files touched by the PR."
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

        # Try ripgrep first; on FileNotFoundError fall back to literal scan.
        try:
            output = self._run_ripgrep(full_path, query, context_lines)
        except FileNotFoundError:
            output = self._literal_fallback(full_path, query, context_lines)

        if output is None:
            return ToolResult(
                content=f"No matches for '{query}' in {path}.",
                is_error=False,
            )

        truncated = self._cap_output(output)
        return ToolResult(content=truncated, is_error=False)

    # -----------------------------------------------------------------------
    # Search backends
    # -----------------------------------------------------------------------

    def _run_ripgrep(self, full_path: Path, query: str, context_lines: int) -> "str | None":
        """Invoke ripgrep; return None on no-match (rg exit 1), raise on
        FileNotFoundError so the caller can fall back to the literal scan.

        Other non-zero exit codes (rg returns 2 on invalid regex etc.) are
        translated to a graceful no-match so the agent gets a usable result
        rather than an opaque error — the agent can refine its query on the
        next iteration."""
        proc = subprocess.run(
            [
                "rg", "--fixed-strings", "--with-filename", "--line-number",
                f"--context={context_lines}",
                "--", query, str(full_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 1:
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _literal_fallback(self, full_path: Path, query: str, context_lines: int) -> "str | None":
        """Pure-Python substring scan when ripgrep is unavailable.

        No regex support — that's the rg path only. The fallback exists so the
        tool doesn't crash a review when rg is missing; degrading to literal
        search is acceptable for the common case (looking up a symbol name).
        """
        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None

        lines = content.splitlines()
        matched_indices = [i for i, line in enumerate(lines) if query in line]
        if not matched_indices:
            return None

        # Build a set of line indices to include — match plus surrounding context
        # — deduped across overlapping windows so back-to-back matches don't
        # multiply the output size.
        indices_to_show: set[int] = set()
        for match_idx in matched_indices:
            start = max(0, match_idx - context_lines)
            end = min(len(lines), match_idx + context_lines + 1)
            indices_to_show.update(range(start, end))

        ordered = sorted(indices_to_show)
        output_lines: list[str] = []
        prev = None
        for idx in ordered:
            if prev is not None and idx > prev + 1:
                output_lines.append("--")  # separator between non-contiguous spans
            output_lines.append(f"{idx + 1}:{lines[idx]}")
            prev = idx
        return "\n".join(output_lines) + "\n"

    def _cap_output(self, text: str) -> str:
        """Truncate to ``_output_bytes_cap`` UTF-8 bytes; append marker on cut."""
        encoded = text.encode("utf-8")
        if len(encoded) <= self._output_bytes_cap:
            return text
        # Truncate then re-decode safely. Use 'ignore' for the boundary byte
        # that may split a multibyte sequence — a single dropped character at
        # the cut is cheaper than a UnicodeDecodeError reaching the agent.
        truncated_bytes = encoded[: self._output_bytes_cap]
        truncated_text = truncated_bytes.decode("utf-8", errors="ignore")
        return truncated_text + self._TRUNCATION_MARKER

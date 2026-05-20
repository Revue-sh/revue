"""Tests for FindCodeTool — REVUE-243 AC2.

ripgrep-based search with surrounding context. Pure-Python literal-string
fallback when ripgrep is missing (typical on dev machines but not all CI
images). Cumulative output capped at 10 KB so an overly-broad query can't
poison the loop budget.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from revue_core.core.tools import FindCodeTool


def test_find_code_returns_matching_span_with_surrounding_context(tmp_path: Path) -> None:
    """Query=`def parse_diff` → result contains the matching line + N lines of
    context. The agent gets the function signature plus enough body to verify
    its claims about it."""
    # Arrange — a realistic small file with one match
    target = tmp_path / "parser.py"
    body_lines = [f"# preamble_{i}" for i in range(1, 60)]
    body_lines.append("def parse_diff(text: str) -> list[Hunk]:")
    body_lines.append("    return _parse_unified(text)")
    body_lines.extend([f"# trailing_{i}" for i in range(1, 60)])
    target.write_text("\n".join(body_lines) + "\n")
    tool = FindCodeTool(repo_root=tmp_path, allowed_paths={"parser.py"})

    # Act
    result = tool.execute(path="parser.py", query="def parse_diff", context_lines=10)

    # Assert
    assert result.is_error is False
    assert "def parse_diff(text: str)" in result.content
    # Surrounding context present
    assert "preamble" in result.content
    assert "trailing" in result.content


def test_find_code_caps_total_output_at_10kb(tmp_path: Path) -> None:
    """Pathological query matching every line of a large file — output must be
    truncated so the agent cannot blow the loop budget with one find_code call."""
    # Arrange — a 5000-line file where every line matches
    target = tmp_path / "big.py"
    target.write_text("\n".join(f"x = {i}" for i in range(5_000)) + "\n")
    tool = FindCodeTool(repo_root=tmp_path, allowed_paths={"big.py"})

    # Act — query matches every line; with 50 lines of context, naive output
    # would be massive.
    result = tool.execute(path="big.py", query="x = ", context_lines=50)

    # Assert — output capped at 10 KB plus a small marker
    assert result.is_error is False
    assert len(result.content.encode("utf-8")) <= 10_500
    assert "truncated" in result.content.lower()


def test_find_code_falls_back_to_literal_when_ripgrep_missing(tmp_path: Path) -> None:
    """If subprocess can't find rg, the tool degrades to a pure-Python literal
    search rather than failing the whole review."""
    # Arrange
    target = tmp_path / "src.py"
    target.write_text("alpha\nbeta\nGAMMA_MARKER\ndelta\nepsilon\n")
    tool = FindCodeTool(repo_root=tmp_path, allowed_paths={"src.py"})

    # Act — simulate ripgrep absent
    with patch("subprocess.run", side_effect=FileNotFoundError("rg not on PATH")):
        result = tool.execute(path="src.py", query="GAMMA_MARKER", context_lines=1)

    # Assert — fallback succeeded, match present, context lines included
    assert result.is_error is False
    assert "GAMMA_MARKER" in result.content
    assert "beta" in result.content
    assert "delta" in result.content


def test_find_code_returns_no_match_message_when_query_not_found(tmp_path: Path) -> None:
    """No matches: result is not an error — it's a successful 'nothing found'.
    The agent uses this to confirm a symbol does NOT appear elsewhere."""
    # Arrange
    target = tmp_path / "src.py"
    target.write_text("alpha\nbeta\ngamma\n")
    tool = FindCodeTool(repo_root=tmp_path, allowed_paths={"src.py"})

    # Act
    result = tool.execute(path="src.py", query="NONEXISTENT_SYMBOL")

    # Assert — not is_error; content describes the null result
    assert result.is_error is False
    assert "no match" in result.content.lower() or "no result" in result.content.lower()


def test_find_code_rejects_path_not_in_allowed_set(tmp_path: Path) -> None:
    """Sandbox parity with ReadFileTool / ReadLinesTool."""
    # Arrange
    target = tmp_path / "other.py"
    target.write_text("alpha\n")
    tool = FindCodeTool(repo_root=tmp_path, allowed_paths={"different.py"})

    # Act
    result = tool.execute(path="other.py", query="alpha")

    # Assert
    assert result.is_error is True
    assert "other.py" in result.content


def test_find_code_tool_definition_has_anthropic_compatible_schema() -> None:
    """tool_definition() yields the JSON schema Anthropic's tool-use API requires."""
    # Arrange / Act
    schema = FindCodeTool.tool_definition()

    # Assert
    assert schema["name"] == "find_code"
    props = schema["input_schema"]["properties"]
    assert set(props.keys()) >= {"path", "query", "context_lines"}
    assert schema["input_schema"]["required"] == ["path", "query"]

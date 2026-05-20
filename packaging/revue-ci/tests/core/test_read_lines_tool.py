"""Tests for ReadLinesTool — REVUE-243 AC1.

Returns ±N lines around a specified line number instead of the whole file.
Reviewer agents know the line from the diff hunk; they shouldn't have to
load a 1500-line file to verify a 20-line context.

Sandbox parity with ReadFileTool: allowed_paths gating, repo_root containment,
symlink rejection.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from revue_core.core.tools import ReadLinesTool


def test_read_lines_returns_window_centred_on_specified_line(tmp_path: Path) -> None:
    """around_line=120, context=10 → result contains lines 110-130 inclusive."""
    # Arrange
    target = tmp_path / "src.py"
    target.write_text("\n".join(f"line_{i}" for i in range(1, 201)) + "\n")
    tool = ReadLinesTool(repo_root=tmp_path, allowed_paths={"src.py"})

    # Act
    result = tool.execute(path="src.py", around_line=120, context=10)

    # Assert
    assert result.is_error is False
    lines = result.content.splitlines()
    # Window is lines 110..130 inclusive → 21 lines
    assert len(lines) == 21
    assert lines[0] == "line_110"
    assert lines[-1] == "line_130"


def test_read_lines_clamps_window_to_start_of_file(tmp_path: Path) -> None:
    """around_line=5 with context=50 must not produce negative line numbers — the
    window clamps to line 1 so the agent gets a usable slice rather than an error."""
    # Arrange
    target = tmp_path / "src.py"
    target.write_text("\n".join(f"line_{i}" for i in range(1, 201)) + "\n")
    tool = ReadLinesTool(repo_root=tmp_path, allowed_paths={"src.py"})

    # Act
    result = tool.execute(path="src.py", around_line=5, context=50)

    # Assert
    assert result.is_error is False
    lines = result.content.splitlines()
    # Lines 1..55 → 55 lines
    assert lines[0] == "line_1"
    assert lines[-1] == "line_55"


def test_read_lines_clamps_window_to_end_of_file(tmp_path: Path) -> None:
    """around_line near EOF: window clamps at the last line, no padding."""
    # Arrange — 100-line file
    target = tmp_path / "src.py"
    target.write_text("\n".join(f"line_{i}" for i in range(1, 101)) + "\n")
    tool = ReadLinesTool(repo_root=tmp_path, allowed_paths={"src.py"})

    # Act — around_line=95, context=20 would request 75..115; clamp to 100.
    result = tool.execute(path="src.py", around_line=95, context=20)

    # Assert
    assert result.is_error is False
    lines = result.content.splitlines()
    assert lines[0] == "line_75"
    assert lines[-1] == "line_100"


def test_read_lines_rejects_path_not_in_allowed_set(tmp_path: Path) -> None:
    """Sandbox parity with ReadFileTool: path must be in the PR's touched files."""
    # Arrange
    target = tmp_path / "other.py"
    target.write_text("line_1\nline_2\nline_3\n")
    tool = ReadLinesTool(repo_root=tmp_path, allowed_paths={"different.py"})

    # Act
    result = tool.execute(path="other.py", around_line=2, context=1)

    # Assert
    assert result.is_error is True
    assert "other.py" in result.content


def test_read_lines_tool_definition_has_anthropic_compatible_schema() -> None:
    """tool_definition() yields the JSON schema Anthropic's tool-use API requires."""
    # Arrange / Act
    schema = ReadLinesTool.tool_definition()

    # Assert
    assert schema["name"] == "read_lines"
    assert isinstance(schema["description"], str) and schema["description"]
    props = schema["input_schema"]["properties"]
    assert set(props.keys()) >= {"path", "around_line", "context"}
    assert schema["input_schema"]["required"] == ["path", "around_line"]

"""Tests for the ReadFileTool that Nova uses to load HEAD file context.

Tool-use addition for REVUE-239: gives Nova surrounding-code visibility so it
can produce coherent code_replacement spans rather than 1-line anchors with
N-line replacements.

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from revue.core.tools import ReadFileTool


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_reads_allowed_pr_file_returns_full_content(tmp_path: Path) -> None:
    """Path inside PR's allowed set: tool returns the file's verbatim content."""
    # Arrange
    target = tmp_path / "src" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("def greet(name: str) -> str:\n    return f'hello {name}'\n")
    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"src/example.py"},
    )

    # Act
    result = tool.execute(path="src/example.py")

    # Assert
    assert result.is_error is False
    assert result.content == "def greet(name: str) -> str:\n    return f'hello {name}'\n"


# ---------------------------------------------------------------------------
# Sandbox — path not in PR diff
# ---------------------------------------------------------------------------


def test_rejects_path_not_in_pr_diff_with_explanatory_error(tmp_path: Path) -> None:
    """Path outside allowed set: tool returns is_error=True and names the constraint."""
    # Arrange
    other = tmp_path / "secrets.env"
    other.write_text("DATABASE_PASSWORD=production-secret\n")
    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"src/touched_by_pr.py"},
    )

    # Act
    result = tool.execute(path="secrets.env")

    # Assert
    assert result.is_error is True
    assert "secrets.env" in result.content
    assert "PR" in result.content  # error message mentions PR file constraint


# ---------------------------------------------------------------------------
# Sandbox — directory traversal
# ---------------------------------------------------------------------------


def test_rejects_dotdot_path_traversal_attempt(tmp_path: Path) -> None:
    """A path containing `..` that resolves outside repo_root: rejected even if listed."""
    # Arrange
    outside = tmp_path.parent / "outside-repo.py"
    outside.write_text("secret_data = 42\n")
    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"../outside-repo.py"},  # pathological allowed entry
    )

    # Act
    result = tool.execute(path="../outside-repo.py")

    # Assert
    assert result.is_error is True
    assert "outside" in result.content.lower() or "repo root" in result.content.lower()


# ---------------------------------------------------------------------------
# Size caps
# ---------------------------------------------------------------------------


def test_returns_error_when_file_byte_size_exceeds_cap(tmp_path: Path) -> None:
    """File over byte cap: tool returns is_error=True with an actionable message."""
    # Arrange
    big_file = tmp_path / "huge.py"
    big_file.write_text("x = 1\n" * 5_000)  # ~30KB
    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"huge.py"},
        max_bytes=10_000,
    )

    # Act
    result = tool.execute(path="huge.py")

    # Assert
    assert result.is_error is True
    assert "too large" in result.content.lower() or "byte" in result.content.lower()
    assert "huge.py" in result.content


def test_returns_error_when_line_count_exceeds_cap(tmp_path: Path) -> None:
    """File over line cap: tool returns is_error=True so Nova can fall back to prose."""
    # Arrange
    long_file = tmp_path / "long.py"
    long_file.write_text("\n".join(f"line_{i} = {i}" for i in range(6_000)) + "\n")
    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"long.py"},
        max_lines=5_000,
        # Explicit max_bytes large enough to ensure the line cap trips first
        # under REVUE-243's tightened default byte cap (65_536).
        max_bytes=1_000_000,
    )

    # Act
    result = tool.execute(path="long.py")

    # Assert
    assert result.is_error is True
    assert "line" in result.content.lower()
    assert "long.py" in result.content


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


def test_returns_error_when_file_does_not_exist(tmp_path: Path) -> None:
    """Allowed path that does not exist on disk: tool returns is_error=True."""
    # Arrange
    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"src/gone.py"},
    )

    # Act
    result = tool.execute(path="src/gone.py")

    # Assert
    assert result.is_error is True
    assert "gone.py" in result.content
    assert "not found" in result.content.lower() or "does not exist" in result.content.lower()


# ---------------------------------------------------------------------------
# Tool definition schema (consumed by Anthropic API)
# ---------------------------------------------------------------------------


def test_rejects_symlink_pointing_to_file_outside_pr(tmp_path: Path) -> None:
    """A symlink at an allowed path that redirects to a non-touched file must be rejected.

    Defence-in-depth: a malicious commit could rename a PR-touched path to a
    symlink whose name appears in allowed_paths but whose target is a
    different file inside the repo (or one outside it). Without the
    post-resolve allowed_paths check, the tool would forward the symlink
    target's content to Nova/Vex, leaking files the PR never touched.
    """
    # Arrange — secrets.env is inside the repo but NOT in the PR's diff.
    secrets = tmp_path / "secrets.env"
    secrets.write_text("DATABASE_PASSWORD=production-secret\n")

    # allowed.py is in the PR diff; we expect it to be a real file.
    # Attacker replaces it with a symlink pointing at secrets.env.
    symlink_path = tmp_path / "allowed.py"
    symlink_path.symlink_to(secrets)

    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"allowed.py"},  # only "allowed.py" is in the PR
    )

    # Act
    result = tool.execute(path="allowed.py")

    # Assert — symlink target (secrets.env) is rejected even though the
    # symlink name is in allowed_paths.
    assert result.is_error is True, (
        f"Symlink redirecting to a non-PR file must be rejected. "
        f"Got content={result.content!r}"
    )
    assert "DATABASE_PASSWORD" not in result.content


def test_accepts_symlink_pointing_to_another_pr_file(tmp_path: Path) -> None:
    """A symlink whose target IS in allowed_paths should still resolve and read.

    Symlinks themselves aren't the threat — only symlinks redirecting to
    non-touched files are. This test guards against over-restriction.
    """
    # Arrange — two PR-touched files; one of them is a symlink to the other.
    real = tmp_path / "real.py"
    real.write_text("def hello(): return 1\n")
    symlink_path = tmp_path / "link.py"
    symlink_path.symlink_to(real)

    tool = ReadFileTool(
        repo_root=tmp_path,
        allowed_paths={"real.py", "link.py"},
    )

    # Act — request via the symlink name.
    result = tool.execute(path="link.py")

    # Assert — resolved target ("real.py") is also in allowed_paths, so allowed.
    assert result.is_error is False
    assert result.content == "def hello(): return 1\n"


# ---------------------------------------------------------------------------
# REVUE-243: Default caps tightened so a single read can't burn ~25 % of the
# 200K context window before tool-loop accumulation. The 2026-05-13 local
# dogfood run on a 13K-line diff confirmed the old defaults (5000 / 200K)
# let three reviewers exceed 202K tokens.
# ---------------------------------------------------------------------------


def test_default_max_lines_is_tightened_to_1500_for_context_safety() -> None:
    """1500 lines ≈ 16K tokens per read — bounded enough that the cumulative
    result cap (AC3) can fire before any single iteration's tool results push
    past the 200K window on their own."""
    # Arrange / Act
    actual = ReadFileTool._DEFAULT_MAX_LINES

    # Assert
    assert actual == 1500, (
        "REVUE-243: _DEFAULT_MAX_LINES must be 1500 (was 5000). "
        "A 5000-line read returns ~40-50K tokens — five such iterations "
        "exceed the 200K window before the model can emit findings."
    )


def test_default_max_bytes_is_tightened_to_64kib_for_context_safety() -> None:
    """64 KiB ≈ 16K tokens. Source of truth for callers that don't pass
    max_bytes explicitly — keeps a single oversized file from poisoning the
    review on its own."""
    # Arrange / Act
    actual = ReadFileTool._DEFAULT_MAX_BYTES

    # Assert
    assert actual == 65_536, (
        "REVUE-243: _DEFAULT_MAX_BYTES must be 65_536 (was 200_000)."
    )


def test_tool_definition_has_anthropic_compatible_schema() -> None:
    """tool_definition() yields the JSON schema Anthropic's tool-use API requires."""
    # Arrange / Act
    schema = ReadFileTool.tool_definition()

    # Assert — top-level required keys
    assert schema["name"] == "read_file"
    assert isinstance(schema["description"], str) and schema["description"]
    assert schema["input_schema"]["type"] == "object"
    # path is the only argument and is required
    assert "path" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["properties"]["path"]["type"] == "string"
    assert schema["input_schema"]["required"] == ["path"]

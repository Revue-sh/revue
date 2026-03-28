#!/usr/bin/env python3
"""Tests for the Revue CLI entry point."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from revue.cli import build_parser, cmd_init, cmd_review, cmd_validate
from revue.core.config_loader import DEFAULT_REVUE_YML
from revue.core.models import FileChange
from revue.core.pipeline import ReviewPipeline, ReviewResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_diff(tmp_path: Path, content: str = "") -> Path:
    """Write a sample .diff file and return its path."""
    p = tmp_path / "sample.diff"
    p.write_text(content or "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n")
    return p


def _write_config(tmp_path: Path, content: str | None = None) -> Path:
    """Write a .revue.yml and return its path."""
    p = tmp_path / ".revue.yml"
    p.write_text(content or 'version: "1"\nai:\n  provider: anthropic\n')
    return p


def _make_file_change(path: str = "app.py", additions: int = 5, deletions: int = 2) -> FileChange:
    return FileChange(
        file_path=path,
        change_type="modified",
        additions=additions,
        deletions=deletions,
        diff="@@ -1,3 +1,4 @@\n-old\n+new\n+added",
    )


def _parse_args(argv: list[str]):
    parser = build_parser()
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 1. test_review_dry_run
# ---------------------------------------------------------------------------

@patch("revue.cli.parse_diff_file")
def test_review_dry_run(mock_parse, tmp_path, capsys):
    diff_file = _write_diff(tmp_path)
    config_file = _write_config(tmp_path)

    mock_parse.return_value = [_make_file_change("src/a.py"), _make_file_change("src/b.py")]

    args = _parse_args(["review", f"--diff={diff_file}", f"--config={config_file}", "--dry-run"])
    rc = cmd_review(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "src/a.py" in out
    assert "src/b.py" in out


# ---------------------------------------------------------------------------
# 2. test_review_missing_diff_file
# ---------------------------------------------------------------------------

def test_review_missing_diff_file(tmp_path, capsys):
    args = _parse_args(["review", f"--diff={tmp_path / 'nonexistent.diff'}"])
    rc = cmd_review(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


# ---------------------------------------------------------------------------
# 3. test_review_invalid_config
# ---------------------------------------------------------------------------

def test_review_invalid_config(tmp_path, capsys):
    diff_file = _write_diff(tmp_path)
    config_file = _write_config(tmp_path, 'version: "1"\nai:\n  provider: bogus_provider\n')

    args = _parse_args(["review", f"--diff={diff_file}", f"--config={config_file}"])
    rc = cmd_review(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "bogus_provider" in err


# ---------------------------------------------------------------------------
# 4. test_review_calls_ai_client
# ---------------------------------------------------------------------------

def test_review_calls_ai_client(tmp_path, capsys):
    diff_file = _write_diff(tmp_path)
    config_file = _write_config(tmp_path)

    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'

    def _factory(config):
        return ReviewPipeline(config, client=mock_client)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}):
        args = _parse_args(["review", f"--diff={diff_file}", f"--config={config_file}"])
        rc = cmd_review(args, pipeline_factory=_factory)

    assert rc == 0
    assert mock_client.complete.call_count >= 1


# ---------------------------------------------------------------------------
# 5. test_review_filter_excludes_files
# ---------------------------------------------------------------------------

@patch("revue.core.pipeline.parse_diff_file")
def test_review_filter_excludes_files(mock_parse, tmp_path, capsys):
    diff_file = _write_diff(tmp_path)
    config_file = _write_config(
        tmp_path,
        'version: "1"\nai:\n  provider: anthropic\nreview:\n  ignore_patterns:\n    - "*.md"\n',
    )

    mock_parse.return_value = [
        _make_file_change("src/app.py"),
        _make_file_change("README.md"),
    ]

    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'

    def _factory(config):
        return ReviewPipeline(config, client=mock_client)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}):
        args = _parse_args(["review", f"--diff={diff_file}", f"--config={config_file}"])
        rc = cmd_review(args, pipeline_factory=_factory)

    assert rc == 0
    # Only app.py should be reviewed, not README.md
    assert mock_client.complete.call_count == 1
    call_prompt = mock_client.complete.call_args[0][0][0]["content"]
    assert "app.py" in call_prompt

    out = capsys.readouterr().out
    assert "1 excluded" in out


# ---------------------------------------------------------------------------
# 6. test_review_cli_provider_override
# ---------------------------------------------------------------------------

@patch("revue.cli.parse_diff_file")
def test_review_cli_provider_override(mock_parse, tmp_path, capsys):
    diff_file = _write_diff(tmp_path)
    # Config says anthropic
    config_file = _write_config(tmp_path, 'version: "1"\nai:\n  provider: anthropic\n')

    mock_parse.return_value = [_make_file_change("a.py")]

    mock_client = MagicMock()
    mock_client.complete.return_value = '{"findings": []}'
    captured_config = {}

    def _factory(config):
        captured_config["provider"] = config.provider
        return ReviewPipeline(config, client=mock_client)

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}):
        args = _parse_args([
            "review",
            f"--diff={diff_file}",
            f"--config={config_file}",
            "--provider=openai",
        ])
        rc = cmd_review(args, pipeline_factory=_factory)

    assert rc == 0
    # factory should have received config with provider=openai (CLI override applied)
    assert captured_config["provider"] == "openai"


# ---------------------------------------------------------------------------
# 7. test_init_creates_file
# ---------------------------------------------------------------------------

def test_init_creates_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = _parse_args(["init"])
    rc = cmd_init(args)

    assert rc == 0
    target = tmp_path / ".revue.yml"
    assert target.exists()
    assert target.read_text() == DEFAULT_REVUE_YML
    assert "Created" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 8. test_init_refuses_overwrite
# ---------------------------------------------------------------------------

def test_init_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".revue.yml").write_text("existing content")

    args = _parse_args(["init"])
    rc = cmd_init(args)

    assert rc == 1
    assert "already exists" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 9. test_init_force_overwrites
# ---------------------------------------------------------------------------

def test_init_force_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".revue.yml").write_text("old content")

    args = _parse_args(["init", "--force"])
    rc = cmd_init(args)

    assert rc == 0
    assert (tmp_path / ".revue.yml").read_text() == DEFAULT_REVUE_YML


# ---------------------------------------------------------------------------
# 10. test_validate_valid_config
# ---------------------------------------------------------------------------

def test_validate_valid_config(tmp_path, capsys):
    config_file = _write_config(tmp_path)
    args = _parse_args(["validate", f"--config={config_file}"])
    rc = cmd_validate(args)

    assert rc == 0
    assert "Config valid" in capsys.readouterr().out

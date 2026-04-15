#!/usr/bin/env python3
"""Tests for the Revue CLI entry point."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from revue.cli import build_parser, cmd_init, cmd_review, cmd_validate
from revue.core.config_loader import DEFAULT_REVUE_YML
from revue.core.license_validator import LicenseInfo
from revue.core.models import FileChange
from revue.core.pipeline import ReviewPipeline, ReviewResult


def _stub_license_info() -> LicenseInfo:
    return LicenseInfo(
        valid=True, tier="pro",
        agents_allowed=["orchestrator", "code-quality-expert", "consolidator"],
        reviews_left=None, expires_at="2027-01-01T00:00:00Z",
        key="test-license-key",
    )


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

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}), \
         patch("revue.core.pipeline.validate_license", return_value=_stub_license_info()), \
         patch("revue.core.pipeline.track_usage"):
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

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}), \
         patch("revue.core.pipeline.validate_license", return_value=_stub_license_info()), \
         patch("revue.core.pipeline.track_usage"):
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

    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}), \
         patch("revue.core.pipeline.validate_license", return_value=_stub_license_info()), \
         patch("revue.core.pipeline.track_usage"):
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


# ---------------------------------------------------------------------------
# REVUE-84: --auto-detect-pr CLI flag tests
# ---------------------------------------------------------------------------

def test_cli_auto_detect_pr_flag_exists():
    """--auto-detect-pr flag is registered in the CLI parser (AC1)."""
    from revue.cli import build_parser
    parser = build_parser()
    # Should not raise
    args = parser.parse_args(["review", "--diff", "fake.diff", "--auto-detect-pr"])
    assert args.auto_detect_pr is True


def test_cli_auto_detect_pr_defaults_false():
    """--auto-detect-pr defaults to False when not provided (AC1)."""
    from revue.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["review", "--diff", "fake.diff"])
    assert args.auto_detect_pr is False


def test_resolve_pr_id_from_env_bitbucket(monkeypatch):
    """Resolves PR ID from BITBUCKET_PR_ID env var (AC1)."""
    from revue.cli import _resolve_pr_id_from_env
    monkeypatch.setenv("BITBUCKET_PR_ID", "42")
    assert _resolve_pr_id_from_env() == 42


def test_resolve_pr_id_from_env_github(monkeypatch):
    """Resolves PR ID from GITHUB_PR_NUMBER env var (AC1)."""
    from revue.cli import _resolve_pr_id_from_env
    monkeypatch.delenv("BITBUCKET_PR_ID", raising=False)
    monkeypatch.setenv("GITHUB_PR_NUMBER", "99")
    assert _resolve_pr_id_from_env() == 99


def test_resolve_pr_id_from_env_gitlab(monkeypatch):
    """Resolves MR IID from CI_MERGE_REQUEST_IID env var (AC1)."""
    from revue.cli import _resolve_pr_id_from_env
    monkeypatch.delenv("BITBUCKET_PR_ID", raising=False)
    monkeypatch.delenv("GITHUB_PR_NUMBER", raising=False)
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", "7")
    assert _resolve_pr_id_from_env() == 7


def test_resolve_pr_id_from_env_none(monkeypatch):
    """Returns None when no PR ID env vars set (AC1)."""
    from revue.cli import _resolve_pr_id_from_env
    monkeypatch.delenv("BITBUCKET_PR_ID", raising=False)
    monkeypatch.delenv("GITHUB_PR_NUMBER", raising=False)
    monkeypatch.delenv("CI_MERGE_REQUEST_IID", raising=False)
    assert _resolve_pr_id_from_env() is None


def test_resolve_pr_id_from_env_non_numeric(monkeypatch):
    """Returns None for non-numeric values (AC1)."""
    from revue.cli import _resolve_pr_id_from_env
    monkeypatch.setenv("BITBUCKET_PR_ID", "not-a-number")
    monkeypatch.delenv("GITHUB_PR_NUMBER", raising=False)
    monkeypatch.delenv("CI_MERGE_REQUEST_IID", raising=False)
    assert _resolve_pr_id_from_env() is None


# ---------------------------------------------------------------------------
# REVUE-86: --pr-description-file flag tests
# ---------------------------------------------------------------------------

def test_cli_pr_description_file_flag_exists():
    """--pr-description-file flag is registered in the CLI parser (AC1)."""
    from revue.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["review", "--diff", "fake.diff",
                              "--pr-description-file", "/tmp/desc.txt"])
    assert args.pr_description_file == "/tmp/desc.txt"


def test_cli_pr_description_file_defaults_none():
    """--pr-description-file defaults to None when not provided (AC1)."""
    from revue.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["review", "--diff", "fake.diff"])
    assert args.pr_description_file is None


def _make_mock_pipeline_ctx(mock_pipeline):
    """Shared mock config factory for pipeline-wired CLI tests."""
    from unittest.mock import MagicMock, patch
    mock_pipeline.run.return_value = ([], [], 0, [])
    mock_cfg = MagicMock(
        output_format="markdown", comment_style=None,
        ignore_patterns=[], max_diff_lines=2000,
    )
    mock_cfg.resolve_api_key = MagicMock()
    return mock_cfg


def test_cli_pr_description_file_read(tmp_path, capsys):
    """CLI reads file, parses it, and passes PRDescription to pipeline (AC2)."""
    import textwrap
    from unittest.mock import MagicMock, patch
    from revue.cli import cmd_review, build_parser
    from revue.core.pr_description_adapter import PRDescription

    desc_file = tmp_path / "pr.txt"
    desc_file.write_text(textwrap.dedent("""\
        ## Summary
        Adds JWT auth.

        ## Out of Scope
        Rate limiting deferred.
    """))
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n")

    parser = build_parser()
    args = parser.parse_args([
        "review", "--diff", str(diff_file),
        "--pr-description-file", str(desc_file),
    ])

    mock_pipeline = MagicMock()
    mock_cfg = _make_mock_pipeline_ctx(mock_pipeline)

    with patch("revue.cli.load_config", return_value=mock_cfg), \
         patch("revue.cli.validate_config", return_value=[]), \
         patch("revue.cli.ReviewPipeline", return_value=mock_pipeline):
        cmd_review(args)

    # Pipeline was called with a real PRDescription, not None
    call_kwargs = mock_pipeline.run.call_args.kwargs
    pr_desc = call_kwargs.get("pr_description")
    assert isinstance(pr_desc, PRDescription)
    assert "JWT auth" in pr_desc.summary
    assert "Rate limiting" in pr_desc.out_of_scope

    captured = capsys.readouterr()
    assert "PR context loaded from file" in captured.out


def test_cli_pr_description_file_missing_graceful(tmp_path, capsys):
    """Missing file logs warning and pipeline still runs with pr_description=None (AC4)."""
    from unittest.mock import MagicMock, patch
    from revue.cli import cmd_review, build_parser

    diff_file = tmp_path / "pr.diff"
    diff_file.write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n")

    parser = build_parser()
    args = parser.parse_args([
        "review", "--diff", str(diff_file),
        "--pr-description-file", "/nonexistent/path/pr.txt",
    ])

    mock_pipeline = MagicMock()
    mock_cfg = _make_mock_pipeline_ctx(mock_pipeline)

    with patch("revue.cli.load_config", return_value=mock_cfg), \
         patch("revue.cli.validate_config", return_value=[]), \
         patch("revue.cli.ReviewPipeline", return_value=mock_pipeline):
        cmd_review(args)

    captured = capsys.readouterr()
    assert "not found" in captured.out
    mock_pipeline.run.assert_called_once()
    # pr_description must be None — graceful degradation
    assert mock_pipeline.run.call_args.kwargs.get("pr_description") is None


def test_cli_pr_context_built_when_description_file_and_pr_id_both_passed(tmp_path):
    """PRContext is not None when --pr-description-file and --pr-id are both given.

    Regression test for the bug where the if-pr_description_file branch was taken
    and resolved_pr_id was never set, causing pr_context=None and reply tracking
    to silently skip. This is the exact CI invocation pattern (REVUE-112).
    """
    from unittest.mock import MagicMock, patch
    from revue.cli import cmd_review, build_parser
    from revue.core.models import PRContext

    desc_file = tmp_path / "pr.txt"
    desc_file.write_text("## Summary\nAdds reply tracking.\n")
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n")

    parser = build_parser()
    args = parser.parse_args([
        "review", "--diff", str(diff_file),
        "--platform", "bitbucket",
        "--pr-id", "43",
        "--workspace", "cbscd",
        "--repo-slug", "revue",
        "--pr-description-file", str(desc_file),
    ])

    mock_pipeline = MagicMock()
    mock_cfg = _make_mock_pipeline_ctx(mock_pipeline)

    with patch("revue.cli.load_config", return_value=mock_cfg), \
         patch("revue.cli.validate_config", return_value=[]), \
         patch("revue.cli.ReviewPipeline", return_value=mock_pipeline):
        cmd_review(args)

    call_kwargs = mock_pipeline.run.call_args.kwargs
    pr_context = call_kwargs.get("pr_context")
    assert pr_context is not None, (
        "pr_context must not be None when --pr-id and --pr-description-file are both passed"
    )
    assert isinstance(pr_context, PRContext)
    assert pr_context.platform == "bitbucket"
    assert pr_context.pr_number == 43
    assert pr_context.repo_owner == "cbscd"
    assert pr_context.repo_name == "revue"


# ---------------------------------------------------------------------------
# REVUE-149: Agent name alongside area label in finding comments
# ---------------------------------------------------------------------------

from revue.cli import _format_finding


def test_format_finding_shows_agent_name_and_category() -> None:
    """Known agent produces 'Maya · Code Quality' label."""
    f = {
        "severity": "low",
        "issue": "Missing type hint",
        "category": "code-quality",
        "agent_name": "maya",
    }
    result = _format_finding(f)
    assert "Maya · Code Quality" in result


def test_format_finding_shows_agent_name_for_all_builtin_agents() -> None:
    """Each built-in agent gets its display name prefixed to the area label."""
    from revue.cli import _AGENT_DISPLAY_NAMES, _CATEGORY_MAP
    cases = [
        ("maya", "code-quality", "Maya · Code Quality"),
        ("zara", "security", "Zara · Security"),
        ("kai", "performance", "Kai · Performance"),
        ("leo", "architecture", "Leo · Architecture"),
    ]
    for agent_name, category, expected_label in cases:
        f = {"severity": "low", "issue": "x", "category": category, "agent_name": agent_name}
        result = _format_finding(f)
        assert expected_label in result, f"Expected '{expected_label}' in output for agent '{agent_name}'"


def test_format_finding_falls_back_gracefully_for_unknown_agent() -> None:
    """Unknown agent name shows category only — no crash, no 'None ·' prefix."""
    f = {
        "severity": "low",
        "issue": "Some issue",
        "category": "code-quality",
        "agent_name": "custom-agent-xyz",
    }
    result = _format_finding(f)
    assert "Code Quality" in result
    assert "None" not in result
    assert "·" not in result


def test_format_finding_category_only_when_no_agent_name() -> None:
    """Finding without agent_name shows area label only (backward compat)."""
    f = {"severity": "low", "issue": "x", "category": "security"}
    result = _format_finding(f)
    assert "Security" in result
    assert "·" not in result

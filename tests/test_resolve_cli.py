"""Tests for src/revue/comments/resolve.py CLI entry point."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from revue_core.comments.resolve import main

# Platform-detection env vars that must be isolated per test. CI runners (e.g.
# Bitbucket Pipelines) set BITBUCKET_PR_ID, which would otherwise leak in and
# win platform detection regardless of the var the test sets.
_PR_CONTEXT_VARS = ("BITBUCKET_PR_ID", "GITHUB_PR_NUMBER", "GITLAB_MR_IID")


def _isolated_env(**overrides: str) -> dict[str, str]:
    """Return os.environ with all PR-context vars stripped, then overrides applied."""
    env = {k: v for k, v in os.environ.items() if k not in _PR_CONTEXT_VARS}
    env.update(overrides)
    return env


class TestNoPRContext:
    """When no PR env vars are set, resolve exits cleanly."""

    def test_no_pr_env_skips(self, capsys, tmp_path):
        """No BITBUCKET_PR_ID / GITHUB_PR_NUMBER / GITLAB_MR_IID → skip."""
        env = {
            k: v
            for k, v in __import__("os").environ.items()
            if k
            not in (
                "BITBUCKET_PR_ID",
                "GITHUB_PR_NUMBER",
                "GITLAB_MR_IID",
            )
        }
        with patch.dict("os.environ", env, clear=True):
            main(["--repo-path", str(tmp_path), "--ticket", "REVUE-99"])

        captured = capsys.readouterr()
        assert "No PR context detected, skipping" in captured.out


class TestWithMockedPlatform:
    """When PR env vars are present, resolve calls process_pr_scan."""

    @patch("revue_core.comments.resolve.CommentResolutionService")
    def test_github_calls_process_pr_scan(self, mock_svc_cls, capsys, tmp_path):
        """GITHUB_PR_NUMBER set → instantiates service and calls process_pr_scan."""
        mock_summary = MagicMock()
        mock_summary.fixed_count = 3
        mock_summary.discussed_count = 1
        mock_summary.remaining_count = 2

        mock_svc = MagicMock()
        mock_svc.process_pr_scan.return_value = mock_summary
        mock_svc_cls.return_value = mock_svc

        env_patch = _isolated_env(
            GITHUB_PR_NUMBER="42",
            GITHUB_REPOSITORY="acme/webapp",
        )
        with patch.dict("os.environ", env_patch, clear=True):
            main(["--repo-path", str(tmp_path), "--ticket", "REVUE-98"])

        mock_svc_cls.assert_called_once_with(str(tmp_path))
        mock_svc.process_pr_scan.assert_called_once()

        call_args = mock_svc.process_pr_scan.call_args
        from revue_core.comments.models import Platform

        assert call_args[0][0] == Platform.GITHUB
        assert call_args[0][1] == "acme"
        assert call_args[0][2] == "webapp"
        assert call_args[0][3] == 42

        captured = capsys.readouterr()
        assert "3 resolved" in captured.out
        assert "1 dismissed" in captured.out
        assert "2 remaining" in captured.out

    @patch("revue_core.comments.resolve.CommentResolutionService")
    def test_bitbucket_calls_process_pr_scan(self, mock_svc_cls, capsys, tmp_path):
        """BITBUCKET_PR_ID set → instantiates service for Bitbucket."""
        mock_summary = MagicMock()
        mock_summary.fixed_count = 0
        mock_summary.discussed_count = 0
        mock_summary.remaining_count = 5

        mock_svc = MagicMock()
        mock_svc.process_pr_scan.return_value = mock_summary
        mock_svc_cls.return_value = mock_svc

        env_patch = _isolated_env(
            BITBUCKET_PR_ID="7",
            BITBUCKET_REPO_OWNER="acme-corp",
            BITBUCKET_REPO_SLUG="api-service",
        )
        with patch.dict("os.environ", env_patch, clear=True):
            main(["--repo-path", str(tmp_path), "--ticket", "REVUE-98"])

        from revue_core.comments.models import Platform

        call_args = mock_svc.process_pr_scan.call_args
        assert call_args[0][0] == Platform.BITBUCKET
        assert call_args[0][3] == 7

    @patch("revue_core.comments.resolve.CommentResolutionService")
    def test_service_exception_does_not_raise(self, mock_svc_cls, capsys, tmp_path):
        """Service failure prints warning but does not raise."""
        mock_svc = MagicMock()
        mock_svc.process_pr_scan.side_effect = RuntimeError("API down")
        mock_svc_cls.return_value = mock_svc

        env_patch = _isolated_env(
            GITHUB_PR_NUMBER="1",
            GITHUB_REPOSITORY="o/r",
        )
        with patch.dict("os.environ", env_patch, clear=True):
            # Should NOT raise
            main(["--repo-path", str(tmp_path), "--ticket", "REVUE-98"])

        captured = capsys.readouterr()
        assert "comment resolution failed" in captured.err

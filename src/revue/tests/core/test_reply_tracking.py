"""Tests for core/reply_tracking/ — ReplyTrackingStrategy protocol and registry.

REVUE-119 AC14: OCP/DIP registry refactor.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pr_context(platform: str = "bitbucket"):
    from revue.core.models import PRContext
    return PRContext(
        platform=platform,
        pr_number=1,
        repo_owner="owner",
        repo_name="repo",
        repo_path="/tmp/repo",
    )


# ---------------------------------------------------------------------------
# T1.1a — Registry returns BitbucketReplyTrackingStrategy for "bitbucket"
# ---------------------------------------------------------------------------

def test_reply_tracking_registry_has_bitbucket_entry():
    from revue.core.reply_tracking import get_strategy
    strategy = get_strategy("bitbucket")
    assert strategy is not None


def test_reply_tracking_registry_bitbucket_returns_service(tmp_path):
    """BitbucketReplyTrackingStrategy.build_wont_fix_svc returns WontFixReplyService
    when credentials are present."""
    from revue.core.reply_tracking import get_strategy
    from revue.comments.service import WontFixReplyService

    strategy = get_strategy("bitbucket")
    ctx = _pr_context("bitbucket")
    ctx.repo_path = str(tmp_path)

    with patch.dict(os.environ, {"BITBUCKET_USERNAME": "u", "BITBUCKET_API_TOKEN": "p"}):
        svc = strategy.build_wont_fix_svc(ctx, ai_client=MagicMock())

    assert isinstance(svc, WontFixReplyService)


def test_reply_tracking_registry_bitbucket_returns_none_when_no_creds(tmp_path):
    """BitbucketReplyTrackingStrategy.build_wont_fix_svc returns None when
    BITBUCKET_USERNAME / BITBUCKET_API_TOKEN are missing."""
    from revue.core.reply_tracking import get_strategy

    strategy = get_strategy("bitbucket")
    ctx = _pr_context("bitbucket")
    ctx.repo_path = str(tmp_path)

    env = {k: v for k, v in os.environ.items()
           if k not in ("BITBUCKET_USERNAME", "BITBUCKET_API_TOKEN")}
    with patch.dict(os.environ, env, clear=True):
        svc = strategy.build_wont_fix_svc(ctx, ai_client=MagicMock())

    assert svc is None


# ---------------------------------------------------------------------------
# T1.1b — Registry returns GitHubReplyTrackingStrategy for "github"
# ---------------------------------------------------------------------------

def test_reply_tracking_registry_has_github_entry():
    from revue.core.reply_tracking import get_strategy
    strategy = get_strategy("github")
    assert strategy is not None


def test_reply_tracking_registry_github_returns_none_when_no_token(tmp_path):
    """GitHubReplyTrackingStrategy.build_wont_fix_svc returns None when GITHUB_TOKEN missing."""
    from revue.core.reply_tracking import get_strategy

    strategy = get_strategy("github")
    ctx = _pr_context("github")
    ctx.repo_path = str(tmp_path)

    env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
    with patch.dict(os.environ, env, clear=True):
        svc = strategy.build_wont_fix_svc(ctx, ai_client=MagicMock())

    assert svc is None


def test_reply_tracking_registry_github_returns_service_when_token_set(tmp_path):
    """GitHubReplyTrackingStrategy.build_wont_fix_svc returns WontFixReplyService
    when GITHUB_TOKEN is set."""
    from revue.core.reply_tracking import get_strategy
    from revue.comments.service import WontFixReplyService

    strategy = get_strategy("github")
    ctx = _pr_context("github")
    ctx.repo_path = str(tmp_path)

    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        svc = strategy.build_wont_fix_svc(ctx, ai_client=MagicMock())

    assert isinstance(svc, WontFixReplyService)


# ---------------------------------------------------------------------------
# T1.1c — Unknown platform → get_strategy returns None (no crash)
# ---------------------------------------------------------------------------

def test_reply_tracking_registry_unknown_platform_returns_none():
    from revue.core.reply_tracking import get_strategy
    assert get_strategy("gitlab") is None
    assert get_strategy("azuredevops") is None
    assert get_strategy("") is None


# ---------------------------------------------------------------------------
# T1.1d — pipeline no longer has _build_wont_fix_svc
# ---------------------------------------------------------------------------

def test_pipeline_does_not_have_build_wont_fix_svc_method():
    """After the registry refactor, _build_wont_fix_svc must be removed."""
    from revue.core.pipeline import ReviewPipeline
    assert not hasattr(ReviewPipeline, "_build_wont_fix_svc"), (
        "_build_wont_fix_svc must be removed — pipeline uses registry now"
    )


# ---------------------------------------------------------------------------
# T6: End-to-end GitHub wire test
# ---------------------------------------------------------------------------

def test_github_reply_strategy_returns_service_with_github_adapter(tmp_path):
    """T6.1: GitHubReplyTrackingStrategy builds a service with GitHub platform/adapter."""
    from revue.core.reply_tracking import get_strategy
    from revue.comments.service import WontFixReplyService

    strategy = get_strategy("github")
    ctx = _pr_context("github")
    ctx.repo_path = str(tmp_path)

    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        svc = strategy.build_wont_fix_svc(ctx, ai_client=MagicMock())

    assert isinstance(svc, WontFixReplyService)
    assert svc._platform == "github"
    # Adapter should be GitHubAdapter (not BitbucketAdapter)
    from revue.comments.platform_adapter import GitHubAdapter
    assert isinstance(svc._adapter, GitHubAdapter)

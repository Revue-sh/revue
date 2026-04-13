"""Reply-tracking strategy registry (REVUE-119 AC14).

OCP/DIP: each platform provides a ReplyTrackingStrategy that knows how to
build a WontFixReplyService.  The pipeline.py uses get_strategy() — no
if/elif platform chains.
"""
from __future__ import annotations

import os
from typing import Any, Optional, Protocol


class ReplyTrackingStrategy(Protocol):
    """Protocol for per-platform reply-tracking strategy."""

    def build_wont_fix_svc(
        self,
        pr_context: Any,
        ai_client: Any,
    ) -> Optional[Any]:
        """Construct a WontFixReplyService for *pr_context*, or None if
        credentials are missing / platform not supported."""
        ...


class BitbucketReplyTrackingStrategy:
    """Bitbucket-specific reply tracking strategy."""

    def build_wont_fix_svc(
        self,
        pr_context: Any,
        ai_client: Any,
    ) -> Optional[Any]:
        from revue.comments.service import WontFixReplyService

        bb_user = os.environ.get("BITBUCKET_USERNAME", "")
        bb_password = os.environ.get("BITBUCKET_API_TOKEN", "")
        if not bb_user or not bb_password:
            print(
                "[revue]   ⚠ Won't-fix reply tracking skipped — "
                "BITBUCKET_USERNAME / BITBUCKET_API_TOKEN not set.",
                flush=True,
            )
            return None
        return WontFixReplyService(
            repo_path=pr_context.repo_path,
            ai_client=ai_client,
            bitbucket_username=bb_user,
            bitbucket_app_password=bb_password,
            repo_owner=pr_context.repo_owner,
            repo_name=pr_context.repo_name,
            platform="bitbucket",
        )


class GitHubReplyTrackingStrategy:
    """GitHub-specific reply tracking strategy."""

    def build_wont_fix_svc(
        self,
        pr_context: Any,
        ai_client: Any,
    ) -> Optional[Any]:
        from revue.comments.service import WontFixReplyService
        from revue.comments.platform_adapter import GitHubAdapter

        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            print(
                "[revue]   ⚠ Won't-fix reply tracking skipped — "
                "GITHUB_TOKEN not set.",
                flush=True,
            )
            return None
        adapter = GitHubAdapter(token)
        return WontFixReplyService(
            repo_path=pr_context.repo_path,
            ai_client=ai_client,
            bitbucket_username="",
            bitbucket_app_password="",
            repo_owner=pr_context.repo_owner,
            repo_name=pr_context.repo_name,
            platform="github",
            adapter=adapter,
        )


# Registry: platform string → strategy instance
_REGISTRY: dict[str, ReplyTrackingStrategy] = {
    "bitbucket": BitbucketReplyTrackingStrategy(),
    "github": GitHubReplyTrackingStrategy(),
}


def get_strategy(platform: str) -> Optional[ReplyTrackingStrategy]:
    """Return the strategy for *platform*, or None if not registered.

    Returns None (not raises) for unknown/unsupported platforms so callers
    can degrade gracefully without exception handling.
    """
    return _REGISTRY.get(platform)

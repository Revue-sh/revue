"""ReplyTrackingStrategy protocol and concrete implementations.

OCP/DIP: pipeline.py looks up a strategy from _REPLY_TRACKING_REGISTRY keyed by
platform string — no if/elif chain.  Adding a new platform requires only a new
class and a registry entry; pipeline.py never changes.

REVUE-119 AC14.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    from revue.core.models import PRContext
    from revue.comments.service import WontFixReplyService

_log = logging.getLogger(__name__)


class ReplyTrackingStrategy(Protocol):
    """Protocol for platform-specific won't-fix reply tracking setup."""

    def build_wont_fix_svc(
        self,
        pr_context: "PRContext",
        ai_client: Any,
    ) -> "Optional[WontFixReplyService]":
        """Construct a WontFixReplyService for the given platform context.

        Returns None (with a warning log) if required credentials are absent.
        """
        ...


class BitbucketReplyTrackingStrategy:
    """Builds WontFixReplyService for Bitbucket PRs.

    Extracted verbatim from pipeline.ReviewPipeline._build_wont_fix_svc.
    """

    def build_wont_fix_svc(
        self,
        pr_context: "PRContext",
        ai_client: Any,
    ) -> "Optional[WontFixReplyService]":
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
    """Builds WontFixReplyService for GitHub PRs.

    REVUE-119: full GitHub reply tracking. Requires GITHUB_TOKEN env var.
    """

    def build_wont_fix_svc(
        self,
        pr_context: "PRContext",
        ai_client: Any,
    ) -> "Optional[WontFixReplyService]":
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
        return WontFixReplyService(
            repo_path=pr_context.repo_path,
            ai_client=ai_client,
            bitbucket_username="",
            bitbucket_app_password="",
            repo_owner=pr_context.repo_owner,
            repo_name=pr_context.repo_name,
            platform="github",
            adapter=GitHubAdapter(token),
        )


_REPLY_TRACKING_REGISTRY: dict[str, ReplyTrackingStrategy] = {
    "bitbucket": BitbucketReplyTrackingStrategy(),
    "github": GitHubReplyTrackingStrategy(),
}

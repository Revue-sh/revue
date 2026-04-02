"""Comment resolution service - orchestrates auto-resolution logic."""
from __future__ import annotations

import re
from typing import Optional

from .models import CommentState, Platform, PRComment, SummaryComment
from .platform_adapter import get_platform_adapter
from .file_store import CommentFileStore


class CommentResolutionService:
    """Business logic for comment auto-resolution."""

    def __init__(self, repo_path: str):
        self.repo = CommentFileStore(repo_path)

    def process_pr_scan(
        self,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int
    ) -> SummaryComment:
        """
        Process comment resolution after PR update.

        This is the main entry point called after Revue scans a PR.

        Steps:
        1. Get all Revue comments for this PR
        2. Check each comment's resolution status on platform
        3. Auto-resolve if code changed
        4. Parse replies for dismissals
        5. Update summary comment
        """
        adapter = get_platform_adapter(platform)
        comments = self.repo.get_comments_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        for comment in comments:
            if comment.state == CommentState.UNRESOLVED:
                self._process_unresolved_comment(comment, adapter)

        # Update summary
        return self._update_summary(
            platform, repo_owner, repo_name, pr_number, adapter
        )

    def _process_unresolved_comment(
        self,
        comment: PRComment,
        adapter
    ) -> None:
        """Process a single unresolved comment."""
        # Check if manually resolved on platform
        if adapter.is_comment_resolved(
            comment.repo_owner,
            comment.repo_name,
            comment.platform_comment_id
        ):
            # Check if there are replies
            replies = adapter.get_comment_replies(
                comment.repo_owner,
                comment.repo_name,
                comment.platform_comment_id
            )

            if replies:
                # Has reply - store it
                reply_text = replies[0].get('body', '')
                self.repo.transition_state(
                    comment.id,
                    CommentState.MANUALLY_RESOLVED_WITH_REPLY,
                    reason="Developer resolved with explanation",
                    developer_reply=reply_text
                )
            else:
                # No reply
                self.repo.transition_state(
                    comment.id,
                    CommentState.MANUALLY_RESOLVED_NO_REPLY,
                    reason="Developer resolved without explanation"
                )
            return

        # Check for developer replies (dismissals)
        replies = adapter.get_comment_replies(
            comment.repo_owner,
            comment.repo_name,
            comment.platform_comment_id
        )

        for reply in replies:
            reply_body = reply.get('body', '').lower()

            # High-confidence dismissal keywords
            if self._is_dismissal(reply_body):
                # Auto-resolve and post acknowledgment
                self._auto_resolve_dismissed(comment, adapter, reply['body'])
                return

        # Check if code changed (placeholder - needs diff analysis)
        # For now, just skip auto-resolve based on code changes
        # This will be implemented in next iteration

    def _is_dismissal(self, reply_text: str) -> bool:
        """
        Check if reply indicates dismissal.

        High-confidence keywords:
        - "won't fix", "wontfix", "not fixing"
        - "keeping as-is", "keeping as is"
        - "intentional"
        """
        patterns = [
            r"\bwon'?t\s+fix\b",
            r"\bwontfix\b",
            r"\bnot\s+fixing\b",
            r"\bkeeping\s+as[-\s]?is\b",
            r"\bintentional\b",
            r"\bnot\s+relevant\b"
        ]

        for pattern in patterns:
            if re.search(pattern, reply_text, re.IGNORECASE):
                return True

        return False

    def _auto_resolve_dismissed(
        self,
        comment: PRComment,
        adapter,
        developer_reply: str
    ) -> None:
        """Auto-resolve a dismissed comment."""
        # Try platform API resolution first
        resolved = adapter.resolve_comment(
            comment.repo_owner,
            comment.repo_name,
            comment.platform_comment_id,
            comment.platform_thread_id
        )

        if not resolved:
            # Fallback: Post acknowledgment comment
            acknowledgment = f"✅ Revue acknowledged: Developer won't fix this. Marking as resolved.\n\n> {developer_reply}"
            adapter.post_reply(
                comment.repo_owner,
                comment.repo_name,
                comment.platform_comment_id,
                comment.platform_thread_id,
                acknowledgment
            )

        # Update state
        self.repo.transition_state(
            comment.id,
            CommentState.DISMISSED_WITH_REASON,
            reason="Developer dismissed with explanation",
            developer_reply=developer_reply
        )

    def _update_summary(
        self,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        adapter
    ) -> SummaryComment:
        """Update or create summary comment."""
        comments = self.repo.get_comments_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        # Calculate counts
        total = len(comments)
        fixed = sum(
            1 for c in comments
            if c.state in [
                CommentState.AUTO_RESOLVED,
                CommentState.MANUALLY_RESOLVED_WITH_REPLY,
                CommentState.MANUALLY_RESOLVED_NO_REPLY
            ]
        )
        discussed = sum(
            1 for c in comments
            if c.state == CommentState.DISMISSED_WITH_REASON
        )
        remaining = sum(
            1 for c in comments
            if c.state == CommentState.UNRESOLVED
        )

        # Get or create summary
        existing_summary = self.repo.get_summary_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        if existing_summary:
            # Update existing
            existing_summary.total_issues = total
            existing_summary.fixed_count = fixed
            existing_summary.discussed_count = discussed
            existing_summary.remaining_count = remaining
            summary = self.repo.create_or_update_summary(existing_summary)

            # Update comment on platform
            adapter.post_reply(
                repo_owner,
                repo_name,
                summary.platform_comment_id,
                None,
                summary.format_summary()
            )
        else:
            # Create new summary comment
            summary_text = f"""🤖 Revue Code Review Summary

📊 **Status:** {total} issues found

This comment will update automatically as you address issues.

---
💬 **How to respond to Revue:**
• Reply "Won't fix" and explain why if you're keeping it as-is
• Reply with a question if you need clarification
• Revue will auto-resolve when you fix the code
"""

            comment_id, thread_id = adapter.post_comment(
                repo_owner,
                repo_name,
                pr_number,
                "README.md",  # Post to README (first file alphabetically)
                1,
                summary_text,
                "HEAD"  # Placeholder - need actual commit SHA
            )

            summary = SummaryComment(
                id=None,
                platform=platform,
                platform_comment_id=comment_id,
                pr_number=pr_number,
                repo_owner=repo_owner,
                repo_name=repo_name,
                total_issues=total,
                fixed_count=fixed,
                discussed_count=discussed,
                remaining_count=remaining,
                last_updated_at=None,
                created_at=None
            )
            summary = self.repo.create_or_update_summary(summary)

        return summary

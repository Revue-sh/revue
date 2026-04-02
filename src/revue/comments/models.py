"""Domain models for comment resolution tracking."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Platform(Enum):
    """VCS platforms supported by Revue."""
    BITBUCKET = "bitbucket"
    GITHUB = "github"
    GITLAB = "gitlab"


class CommentState(Enum):
    """Comment resolution states."""
    UNRESOLVED = "unresolved"
    AUTO_RESOLVED = "auto_resolved"
    MANUALLY_RESOLVED_WITH_REPLY = "manually_resolved_with_reply"
    MANUALLY_RESOLVED_NO_REPLY = "manually_resolved_no_reply"
    DISMISSED_WITH_REASON = "dismissed_with_reason"
    IGNORED = "ignored"


@dataclass
class PRComment:
    """Represents a comment posted by Revue on a PR/MR."""
    id: int | None
    platform: Platform
    platform_comment_id: str
    platform_thread_id: str | None
    pr_number: int
    repo_owner: str
    repo_name: str
    file_path: str
    line_number: int
    comment_body: str
    finding_id: int | None
    state: CommentState
    created_at: datetime
    updated_at: datetime


@dataclass
class CommentStateTransition:
    """Audit trail for comment state changes."""
    id: int | None
    comment_id: int
    from_state: CommentState | None
    to_state: CommentState
    reason: str | None
    developer_reply: str | None
    transition_at: datetime


@dataclass
class SummaryComment:
    """Live progress summary comment (first comment on PR)."""
    id: int | None
    platform: Platform
    platform_comment_id: str
    pr_number: int
    repo_owner: str
    repo_name: str
    total_issues: int
    fixed_count: int
    discussed_count: int
    remaining_count: int
    last_updated_at: datetime
    created_at: datetime
    
    @property
    def progress_percentage(self) -> int:
        """Calculate progress percentage."""
        if self.total_issues == 0:
            return 0
        return int((self.fixed_count + self.discussed_count) / self.total_issues * 100)
    
    def format_summary(self) -> str:
        """Generate formatted summary text for posting to PR."""
        progress_bar = self._generate_progress_bar()
        
        return f"""🤖 Revue Code Review — Updated {self._format_time_ago()}

Progress: {progress_bar} {self.progress_percentage}% complete

✅ {self.fixed_count} fixed
💬 {self.discussed_count} discussed
⏳ {self.remaining_count} remaining
🔍 Total reviewed: {self.total_issues} issues

---
{self._encouragement_message()}
"""
    
    def _generate_progress_bar(self, length: int = 10) -> str:
        """Generate unicode progress bar."""
        filled = int(self.progress_percentage / 100 * length)
        return "█" * filled + "░" * (length - filled)
    
    def _format_time_ago(self) -> str:
        """Format last update as relative time."""
        now = datetime.now(timezone.utc)
        delta = now - self.last_updated_at
        
        if delta.seconds < 60:
            return "just now"
        elif delta.seconds < 3600:
            minutes = delta.seconds // 60
            return f"{minutes} min ago"
        elif delta.seconds < 86400:
            hours = delta.seconds // 3600
            return f"{hours}h ago"
        else:
            return f"{delta.days}d ago"
    
    def _encouragement_message(self) -> str:
        """Generate encouraging message based on progress."""
        if self.progress_percentage == 100:
            return "🎉 All issues resolved! Ready to merge."
        elif self.progress_percentage >= 80:
            return f"Great progress! 🎉 {self.remaining_count} issues left before merge."
        elif self.progress_percentage >= 50:
            return f"Keep going! {self.remaining_count} issues remaining."
        else:
            return f"{self.remaining_count} issues to review."

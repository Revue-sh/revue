"""Domain models for comment resolution tracking and pipeline contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Protocol


class Platform(Enum):
    """VCS platforms supported by Revue."""
    BITBUCKET = "bitbucket"
    GITHUB = "github"
    GITLAB = "gitlab"


class CommentState(Enum):
    """Comment resolution states."""
    ACTIVE = "active"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AUTO_RESOLVED = "auto_resolved"
    MANUALLY_RESOLVED_WITH_REPLY = "manually_resolved_with_reply"
    MANUALLY_RESOLVED_NO_REPLY = "manually_resolved_no_reply"
    DISMISSED_WITH_REASON = "dismissed_with_reason"
    IGNORED = "ignored"
    WONT_FIX = "wont_fix"  # Developer acknowledged + stated reason (Story B, REVUE-112)


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
    finding_fingerprint: str | None = None


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
    revision: int = 1
    
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


# ---------------------------------------------------------------------------
# Pipeline contracts (REVUE-208)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    """One agent's contribution to a finding. Immutable once created."""
    agent_name: str
    category: str

    def __post_init__(self) -> None:
        if not self.agent_name:
            raise ValueError("Attribution.agent_name must be non-empty")


_VALID_SEVERITIES: frozenset[str] = frozenset({"high", "medium", "low", "info"})
_VALID_GROUP_TYPES: frozenset[str] = frozenset({"singleton", "proximity", "same_line"})


@dataclass
class AgentFinding:
    """Raw finding output from a single agent."""
    file_path: str
    line_number: int
    severity: Literal["high", "medium", "low", "info"]
    issue: str
    suggestion: str
    confidence: float
    category: str
    agent_name: str
    code_replacement: list[str] | None
    replacement_line_count: int
    snippet: str = ""
    language: str = "unknown"  # detected from file extension

    def __post_init__(self) -> None:
        if not self.file_path:
            raise ValueError("AgentFinding.file_path must be non-empty")
        if self.line_number <= 0:
            raise ValueError(f"AgentFinding.line_number must be > 0, got {self.line_number}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"AgentFinding.confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(f"AgentFinding.severity must be one of {_VALID_SEVERITIES}, got {self.severity!r}")
        if not self.agent_name:
            raise ValueError("AgentFinding.agent_name must be non-empty")


@dataclass
class SynthesisGroup:
    """Intermediate grouping produced by GroupingStrategy.group()."""
    findings: list[AgentFinding]
    file_path: str
    line_range: tuple[int, int]
    group_type: Literal["singleton", "proximity", "same_line"]

    def __post_init__(self) -> None:
        if not self.file_path:
            raise ValueError("SynthesisGroup.file_path must be non-empty")
        if not self.findings:
            raise ValueError("SynthesisGroup.findings must contain at least one AgentFinding")
        if self.line_range[0] <= 0 or self.line_range[1] <= 0:
            raise ValueError(
                f"SynthesisGroup.line_range must contain positive line numbers, got {self.line_range}"
            )
        if self.line_range[0] > self.line_range[1]:
            raise ValueError(
                f"SynthesisGroup.line_range must be ordered (start ≤ end), got {self.line_range}"
            )
        if self.group_type not in _VALID_GROUP_TYPES:
            raise ValueError(
                f"SynthesisGroup.group_type must be one of {_VALID_GROUP_TYPES}, got {self.group_type!r}"
            )


@dataclass
class ConsolidatedFinding:
    """Final typed finding ready for BodyBuilder.

    attribution is required and non-nullable — structural guarantee that
    no comment can be posted without knowing which agent(s) raised it
    (fixes the MR !22 attribution-drop regressions).
    """
    file_path: str
    line_number: int
    severity: Literal["high", "medium", "low", "info"]
    issue: str
    suggestion: str
    confidence: float
    category: str
    attribution: list[Attribution]
    code_replacement: list[str] | None
    replacement_line_count: int
    snippet: str
    group_type: Literal["singleton", "proximity", "same_line"] = "singleton"

    def __post_init__(self) -> None:
        if not self.file_path:
            raise ValueError("ConsolidatedFinding.file_path must be non-empty")
        if self.line_number <= 0:
            raise ValueError(f"ConsolidatedFinding.line_number must be > 0, got {self.line_number}")
        if not self.attribution:
            raise ValueError("ConsolidatedFinding.attribution must contain at least one Attribution")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"ConsolidatedFinding.confidence must be in [0.0, 1.0], got {self.confidence}")
        if self.group_type not in _VALID_GROUP_TYPES:
            raise ValueError(
                f"ConsolidatedFinding.group_type must be one of {_VALID_GROUP_TYPES}, got {self.group_type!r}"
            )


# ---------------------------------------------------------------------------
# Strategy Protocols (REVUE-208 — Decision 4)
# All three live in this module alongside the data types they operate on.
# ---------------------------------------------------------------------------


class GroupingStrategy(Protocol):
    """Pass A: cluster raw agent findings into SynthesisGroups."""

    def group(self, findings: list[AgentFinding]) -> list[SynthesisGroup]: ...


class SynthesisStrategy(Protocol):
    """Pass B: synthesise a SynthesisGroup into a ConsolidatedFinding.

    On LLM failure, implementors must fall back to deterministic concatenation
    with full attribution preserved — callers cannot observe which path ran.
    """

    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding: ...


class FindingPostProcessor(Protocol):
    """Transform or validate a ConsolidatedFinding.

    Return None to drop the finding from the inline stream.
    Return the (possibly modified) finding to keep it.
    """

    def process(self, finding: ConsolidatedFinding) -> ConsolidatedFinding | None: ...


# ---------------------------------------------------------------------------
# HunkTracker contracts (REVUE-211)
# ---------------------------------------------------------------------------


class HunkState(Enum):
    """State machine states for prior-comment resolution tracking.

    Each prior finding follows one of 14 legal paths through these states.
    Terminal states (AUTO_RESOLVED, PLATFORM_RESOLVED) cannot transition further.
    """
    INITIAL = "initial"
    UNTOUCHED = "untouched"
    CODE_REMOVED = "code_removed"
    CHANGED = "changed"
    NOVA_CALLED = "nova_called"
    FULLY_ADDRESSED = "fully_addressed"
    NOT_FULLY_ADDRESSED = "not_fully_addressed"
    NOVA_ERROR = "nova_error"
    REPLY_FAILED = "reply_failed"
    FOLLOW_UP_POSTED = "follow_up_posted"
    RESOLVE_REPLY_POSTED = "resolve_reply_posted"
    AUTO_RESOLVED = "auto_resolved"       # terminal — Revue confirmed fix
    PLATFORM_RESOLVED = "platform_resolved"  # terminal — human closed thread


class ResolutionVerdict(Enum):
    """Verdict returned by ResolutionStrategy: how well the finding was addressed."""
    FULLY = "fully"
    PARTIAL = "partial"
    UNRESOLVED = "not"


@dataclass(frozen=True)
class ResolutionResult:
    """Return type for ResolutionStrategy.resolve(): verdict + actionable guidance.

    ``guidance`` is a human-readable sentence extracted from Nova's analysis,
    used verbatim in the follow-up reply posted to the developer's thread.
    """
    verdict: ResolutionVerdict
    guidance: str


class ResolutionStrategy(Protocol):
    """Semantic analysis: determine whether a finding is addressed in new code."""

    def resolve(
        self,
        original_finding: dict,
        new_hunk: str,
        prior_follow_up: str | None = None,
    ) -> ResolutionResult:
        """Return a ResolutionResult with verdict and actionable guidance."""
        ...

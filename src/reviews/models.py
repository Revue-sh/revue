"""Domain models for review intelligence."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Review:
    """Core review run domain model."""

    id: int
    ticket_id: str
    branch: str
    model: str
    tier: str
    created_at: datetime
    finding_count: int = 0


@dataclass
class FindingSummary:
    """Lightweight finding summary for list views."""

    id: int
    severity: str
    file_path: str
    issue: str
    mode: str  # baseline or contextual


@dataclass
class ReviewDetail:
    """Detailed review with findings."""

    review: Review
    findings: list[FindingSummary]
    pr_description: Optional[str] = None

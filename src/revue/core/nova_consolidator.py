"""
Nova consolidation — deduplicate and prioritise findings (Story [007]).

SRP: consolidation only.
OCP: deduplication strategies are pluggable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .models import AIReview


class DeduplicationStrategy(Protocol):
    """Pluggable deduplication strategy (OCP)."""
    def are_duplicates(self, a: AIReview, b: AIReview) -> bool: ...


class SameFileLineStrategy:
    """Same file + line + same severity = duplicate."""
    def are_duplicates(self, a: AIReview, b: AIReview) -> bool:
        return (
            a.file_path == b.file_path
            and a.line_number == b.line_number
            and a.severity == b.severity
        )


class SimilarIssueStrategy:
    """Same file, nearby line, high word overlap in issue text = duplicate."""
    _LINE_PROXIMITY = 3
    _OVERLAP_THRESHOLD = 0.6
    _STOPWORDS = {"a", "an", "the", "is", "in", "at", "to", "for", "of", "this", "that"}

    def are_duplicates(self, a: AIReview, b: AIReview) -> bool:
        if a.file_path != b.file_path:
            return False
        if abs(a.line_number - b.line_number) > self._LINE_PROXIMITY:
            return False
        words_a = set(a.issue.lower().split()) - self._STOPWORDS
        words_b = set(b.issue.lower().split()) - self._STOPWORDS
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap >= self._OVERLAP_THRESHOLD


_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "suggestion": 3, "info": 4}

_DEFAULT_STRATEGIES: list[DeduplicationStrategy] = [
    SameFileLineStrategy(),
    SimilarIssueStrategy(),
]


@dataclass
class ConsolidationResult:
    findings: list[AIReview]
    duplicates_removed: int
    original_count: int

    @property
    def deduplication_ratio(self) -> float:
        if self.original_count == 0:
            return 0.0
        return self.duplicates_removed / self.original_count


def consolidate(
    findings: list[AIReview],
    strategies: list[DeduplicationStrategy] | None = None,
    min_confidence: float = 0.0,
) -> ConsolidationResult:
    """
    Deduplicate and prioritise findings.

    - Remove duplicates using strategies (keep highest-confidence finding)
    - Filter out findings below min_confidence threshold
    - Sort: critical → major → minor → suggestion, then by confidence desc
    - Never raises
    """
    active = strategies if strategies is not None else _DEFAULT_STRATEGIES
    original_count = len(findings)

    # Deduplicate: for each group of duplicates, keep highest confidence
    kept: list[AIReview] = []
    removed = 0

    for candidate in findings:
        is_dup = False
        for i, existing in enumerate(kept):
            for strategy in active:
                try:
                    if strategy.are_duplicates(candidate, existing):
                        # Keep whichever has higher confidence
                        if candidate.confidence > existing.confidence:
                            kept[i] = candidate
                        removed += 1
                        is_dup = True
                        break
                except Exception:
                    pass
            if is_dup:
                break
        if not is_dup:
            kept.append(candidate)

    # Filter by confidence
    filtered = [f for f in kept if f.confidence >= min_confidence]
    removed += len(kept) - len(filtered)

    # Sort by severity then confidence desc
    sorted_findings = sorted(
        filtered,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), -f.confidence),
    )

    return ConsolidationResult(
        findings=sorted_findings,
        duplicates_removed=removed,
        original_count=original_count,
    )

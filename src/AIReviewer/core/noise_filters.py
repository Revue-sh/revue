"""
Noise filters — suppress false positives (Story [008]).

SRP: filtering only.
OCP: filters are pluggable NoiseFilter Protocol implementations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import AIReview


class NoiseFilter(Protocol):
    """Pluggable noise filter (OCP — add filters without modifying this file)."""
    name: str
    def should_suppress(self, review: AIReview) -> bool: ...


@dataclass
class FilterResult:
    kept: list[AIReview]
    suppressed: list[tuple[AIReview, str]]  # (review, filter_name)

    @property
    def suppressed_count(self) -> int:
        return len(self.suppressed)

    @property
    def kept_count(self) -> int:
        return len(self.kept)


class LowConfidenceFilter:
    """Suppress findings below a confidence threshold."""
    name = "low-confidence"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def should_suppress(self, review: AIReview) -> bool:
        return review.confidence < self.threshold


class TestFileFilter:
    """Suppress non-critical findings in test files."""
    name = "test-file"
    _TEST_PATTERNS = ("test_", "_test.", "spec_", "_spec.", "/tests/", "/test/")

    def should_suppress(self, review: AIReview) -> bool:
        path = review.file_path.lower()
        if review.severity == "critical":
            return False
        return any(pattern in path for pattern in self._TEST_PATTERNS)


class GeneratedFileFilter:
    """Suppress all findings in auto-generated files."""
    name = "generated-file"
    _GENERATED_PATTERNS = (
        ".min.js", ".min.css", "package-lock.json", "yarn.lock",
        ".pb.go", "_pb2.py", ".generated.", "/__generated__/",
    )

    def should_suppress(self, review: AIReview) -> bool:
        path = review.file_path.lower()
        return any(pattern in path for pattern in self._GENERATED_PATTERNS)


class EmptyIssueFilter:
    """Suppress findings with blank issue text."""
    name = "empty-issue"

    def should_suppress(self, review: AIReview) -> bool:
        return not review.issue.strip()


_DEFAULT_FILTERS: list[NoiseFilter] = [
    LowConfidenceFilter(threshold=0.5),
    TestFileFilter(),
    GeneratedFileFilter(),
    EmptyIssueFilter(),
]


def apply_noise_filters(
    findings: list[AIReview],
    filters: list[NoiseFilter] | None = None,
) -> FilterResult:
    """
    Apply noise filters to a list of findings.

    - Each finding is tested against all filters
    - First matching filter suppresses the finding (short-circuit)
    - Returns FilterResult with kept and suppressed lists
    - Never raises
    """
    active = filters if filters is not None else _DEFAULT_FILTERS
    kept: list[AIReview] = []
    suppressed: list[tuple[AIReview, str]] = []

    for review in findings:
        suppressed_by: str | None = None
        for f in active:
            try:
                if f.should_suppress(review):
                    suppressed_by = f.name
                    break
            except Exception:
                pass
        if suppressed_by:
            suppressed.append((review, suppressed_by))
        else:
            kept.append(review)

    return FilterResult(kept=kept, suppressed=suppressed)

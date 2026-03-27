"""
Contradiction detection between specialist agent findings (Story [005]).

SRP: detects contradictions only — resolution is in contradiction_resolver.py (Story [006]).
OCP: detection strategies are pluggable via ContradictionStrategy Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import AIReview


class ContradictionStrategy(Protocol):
    """Pluggable strategy for detecting contradictions (OCP)."""

    def are_contradictory(self, a: AIReview, b: AIReview) -> bool: ...


@dataclass
class Contradiction:
    """A detected contradiction between two findings."""
    finding_a: AIReview
    finding_b: AIReview
    reason: str
    agent_a: str = ""  # populated if agent attribution is available
    agent_b: str = ""


@dataclass
class ContradictionDetectionResult:
    contradictions: list[Contradiction]
    total_findings: int

    @property
    def has_contradictions(self) -> bool:
        return bool(self.contradictions)

    @property
    def contradiction_count(self) -> int:
        return len(self.contradictions)


class SameLineSameFileStrategy:
    """Detect contradictions: two findings on the same file+line with opposing severities."""

    # Pairs that are considered contradictory (high vs low confidence)
    _OPPOSING: dict[str, set[str]] = {
        "critical": {"suggestion", "minor"},
        "major": {"suggestion"},
        "minor": {"critical"},
        "suggestion": {"critical", "major"},
    }

    def are_contradictory(self, a: AIReview, b: AIReview) -> bool:
        if a.file_path != b.file_path or a.line_number != b.line_number:
            return False
        return b.severity in self._OPPOSING.get(a.severity, set())


class SimilarIssueOpposingConfidenceStrategy:
    """Detect contradictions: same file, nearby lines, similar issue text but very different confidence."""

    _CONFIDENCE_THRESHOLD = 0.5  # difference > 0.5 is suspicious
    _LINE_PROXIMITY = 5

    def are_contradictory(self, a: AIReview, b: AIReview) -> bool:
        if a.file_path != b.file_path:
            return False
        if abs(a.line_number - b.line_number) > self._LINE_PROXIMITY:
            return False
        confidence_diff = abs(a.confidence - b.confidence)
        if confidence_diff < self._CONFIDENCE_THRESHOLD:
            return False
        # Simple overlap check: share at least one significant word
        words_a = set(a.issue.lower().split())
        words_b = set(b.issue.lower().split())
        _STOPWORDS = {"a", "an", "the", "is", "in", "at", "to", "for", "of", "this"}
        overlap = (words_a - _STOPWORDS) & (words_b - _STOPWORDS)
        return bool(overlap)


_DEFAULT_STRATEGIES: list[ContradictionStrategy] = [
    SameLineSameFileStrategy(),
    SimilarIssueOpposingConfidenceStrategy(),
]


def detect_contradictions(
    findings: list[AIReview],
    strategies: list[ContradictionStrategy] | None = None,
) -> ContradictionDetectionResult:
    """
    Detect contradictions in a list of findings using provided strategies.

    - Compares all pairs (O(n²) — acceptable for typical review sizes <200 findings)
    - Uses first matching strategy to determine contradiction reason
    - Returns ContradictionDetectionResult; never raises
    """
    active_strategies = strategies if strategies is not None else _DEFAULT_STRATEGIES
    contradictions: list[Contradiction] = []
    seen_pairs: set[tuple[int, int]] = set()

    for i, a in enumerate(findings):
        for j, b in enumerate(findings):
            if i >= j:
                continue
            pair = (i, j)
            if pair in seen_pairs:
                continue
            for strategy in active_strategies:
                try:
                    if strategy.are_contradictory(a, b):
                        contradictions.append(Contradiction(
                            finding_a=a,
                            finding_b=b,
                            reason=type(strategy).__name__,
                        ))
                        seen_pairs.add(pair)
                        break  # one contradiction per pair
                except Exception:
                    pass  # strategy failure is non-fatal

    return ContradictionDetectionResult(
        contradictions=contradictions,
        total_findings=len(findings),
    )

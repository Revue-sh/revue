"""
Contradiction resolution via AI orchestrator (Story [006]).

SRP: resolves contradictions only — detection is in contradiction_detector.py.
DIP: depends on AIClient Protocol, not a concrete client.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_client import AIClient

from .models import AIReview
from .contradiction_detector import Contradiction, ContradictionDetectionResult


RESOLUTION_PROMPT = """\
You are a senior code reviewer resolving a contradiction between two AI agents.

Finding A (severity: {severity_a}, confidence: {confidence_a:.0%}):
File: {file_a}, Line: {line_a}
Issue: {issue_a}

Finding B (severity: {severity_b}, confidence: {confidence_b:.0%}):
File: {file_b}, Line: {line_b}
Issue: {issue_b}

Determine which finding is more accurate, or synthesise a combined finding.
Respond with JSON only:
{{
  "winner": "A" | "B" | "both" | "neither",
  "severity": "critical|major|minor|suggestion",
  "issue": "final issue description",
  "confidence": 0.0-1.0,
  "rationale": "brief explanation"
}}"""


@dataclass
class ResolutionResult:
    original_a: AIReview
    original_b: AIReview
    resolved: AIReview | None  # None if "neither"
    winner: str                # "A", "B", "both", "neither"
    rationale: str
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error


@dataclass
class ContradictionResolutionResult:
    resolutions: list[ResolutionResult]
    unresolved: list[Contradiction]  # fallback: kept as-is if resolution failed

    @property
    def resolved_findings(self) -> list[AIReview]:
        findings: list[AIReview] = []
        for r in self.resolutions:
            if r.resolved is not None:
                findings.append(r.resolved)
        return findings


def resolve_contradictions(
    detection_result: ContradictionDetectionResult,
    all_findings: list[AIReview],
    client: "AIClient",
) -> tuple[list[AIReview], ContradictionResolutionResult]:
    """
    Resolve detected contradictions via AI and return deduplicated findings.

    Returns:
        (final_findings, resolution_result)
        final_findings = all_findings minus contradicted pairs + resolved replacements
    """
    import json

    resolutions: list[ResolutionResult] = []
    unresolved: list[Contradiction] = []
    # Track indices to remove from all_findings
    to_remove: set[int] = set()

    for contradiction in detection_result.contradictions:
        a, b = contradiction.finding_a, contradiction.finding_b
        prompt = RESOLUTION_PROMPT.format(
            severity_a=a.severity, confidence_a=a.confidence,
            file_a=a.file_path, line_a=a.line_number, issue_a=a.issue,
            severity_b=b.severity, confidence_b=b.confidence,
            file_b=b.file_path, line_b=b.line_number, issue_b=b.issue,
        )
        try:
            raw = client.complete([{"role": "user", "content": prompt}])
            data = json.loads(raw)
            winner = data.get("winner", "A")
            resolved_review: AIReview | None = None

            if winner != "neither":
                resolved_review = AIReview(
                    file_path=a.file_path,
                    line_number=a.line_number,
                    severity=data.get("severity", a.severity),
                    issue=data.get("issue", a.issue),
                    suggestion=a.suggestion,
                    confidence=float(data.get("confidence", a.confidence)),
                    category=a.category,
                )

            # Mark originals for removal
            for idx, f in enumerate(all_findings):
                if f is a or f is b:
                    to_remove.add(idx)

            resolutions.append(ResolutionResult(
                original_a=a, original_b=b,
                resolved=resolved_review,
                winner=winner,
                rationale=data.get("rationale", ""),
            ))
        except Exception as exc:
            unresolved.append(contradiction)
            resolutions.append(ResolutionResult(
                original_a=a, original_b=b,
                resolved=None, winner="neither",
                rationale="", error=str(exc),
            ))

    # Build final findings: keep non-contradicted + add resolved
    final: list[AIReview] = [f for i, f in enumerate(all_findings) if i not in to_remove]
    resolution_result = ContradictionResolutionResult(resolutions=resolutions, unresolved=unresolved)
    final.extend(resolution_result.resolved_findings)

    return final, resolution_result

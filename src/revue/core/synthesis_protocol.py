"""ContradictionSynthesiser protocol — DIP boundary for contradiction synthesis (REVUE-180)."""
from __future__ import annotations

from typing import Protocol

from .models import AIReview


class ContradictionSynthesiser(Protocol):
    """Synthesise contradictory findings from multiple agents into unified findings."""

    def synthesise(
        self, findings: list[AIReview]
    ) -> tuple[list[AIReview], list[dict]]:
        """Synthesise findings, returning (updated_findings, synthesis_events).

        On failure, implementations must return (original_findings, []) — never raise
        past this boundary.
        """
        ...

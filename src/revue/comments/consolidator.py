"""Stub module for the Consolidator pipeline stage (REVUE-208).

Full implementation delivered in REVUE-213.
Architecture spec: docs/architecture/comment-posting.md
"""
from __future__ import annotations


class Consolidator:
    """Orchestrates Pass A (GroupingStrategy) + Pass B (SynthesisStrategy) + post-processor chain."""

    ...


class ProximityAndCountGroupingStrategy:
    """Pass A: cluster findings where line_distance ≤ N and group_size ≤ K (Decision 2)."""

    ...


class NovaSingleShotStrategy:
    """Pass B: single-shot Nova synthesis for non-singleton groups (Decision 3)."""

    ...


class NoOpSuggestionDropper:
    """Post-processor: set code_replacement=None when it equals the snippet (Decision 5)."""

    ...


class UnanchoredFindingExtractor:
    """Post-processor: demote findings without anchor evidence to summary_sink (Decision 6)."""

    ...

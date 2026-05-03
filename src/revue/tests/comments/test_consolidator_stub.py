"""Import and class-existence tests for consolidator.py stub (REVUE-208 AC3)."""
from __future__ import annotations

from revue.comments.consolidator import (
    Consolidator,
    NoOpSuggestionDropper,
    NovaSingleShotStrategy,
    ProximityAndCountGroupingStrategy,
    UnanchoredFindingExtractor,
)


def test_consolidator_importable() -> None:
    assert Consolidator is not None


def test_proximity_grouper_importable() -> None:
    assert ProximityAndCountGroupingStrategy is not None


def test_nova_strategy_importable() -> None:
    assert NovaSingleShotStrategy is not None


def test_noop_dropper_importable() -> None:
    assert NoOpSuggestionDropper is not None


def test_unanchored_extractor_importable() -> None:
    assert UnanchoredFindingExtractor is not None

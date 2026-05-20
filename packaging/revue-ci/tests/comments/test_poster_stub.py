"""Import and class-existence tests for poster.py stub (REVUE-208 AC3)."""
from __future__ import annotations

from revue_core.comments.poster import Poster


def test_poster_importable() -> None:
    assert Poster is not None

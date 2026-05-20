"""Import and class-existence tests for body_builder.py stub (REVUE-208 AC3)."""
from __future__ import annotations

from revue_core.comments.body_builder import BodyBuilder


def test_body_builder_importable() -> None:
    assert BodyBuilder is not None

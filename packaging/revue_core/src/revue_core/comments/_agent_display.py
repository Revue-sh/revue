"""Backwards-compatibility re-export.

The canonical agent + severity display data lives in ``core/display.py``
so any layer can import it without crossing the comments-package boundary
backwards. This module preserves the old import path used by
``body_builder.py`` and ``summary_builder.py``.
"""
from __future__ import annotations

from ..core.display import AGENT_DISPLAY_NAMES, AGENT_EMOJIS

__all__ = ["AGENT_DISPLAY_NAMES", "AGENT_EMOJIS"]

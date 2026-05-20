"""Reply tracking strategy registry (REVUE-119 AC14)."""
from __future__ import annotations

from .strategies import ReplyTrackingStrategy, _REPLY_TRACKING_REGISTRY


def get_strategy(platform: str) -> "ReplyTrackingStrategy | None":
    """Return the ReplyTrackingStrategy for *platform*, or None if unsupported."""
    return _REPLY_TRACKING_REGISTRY.get(platform)

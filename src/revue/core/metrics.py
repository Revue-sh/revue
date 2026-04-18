#!/usr/bin/env python3
"""MetricsCollector Protocol and implementations — REVUE-154."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MetricsEvent:
    """Token usage event captured from a single AI client call."""

    event_type: str
    timestamp: str
    agent_name: str | None
    provider: str
    model: str
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class MetricsCollector(Protocol):
    """Protocol for collecting and storing metrics events."""

    def record(self, event: MetricsEvent) -> None:
        """Record a single metrics event."""
        ...

    def flush(self, run_id: str) -> None:
        """Flush collected metrics — write to persistent store if applicable."""
        ...

    def verbose_summary(self) -> dict | None:
        """Return in-memory totals after flush, or None if unavailable."""
        ...


class NullMetricsCollector:
    """Default no-op metrics collector (used when metrics disabled)."""

    def record(self, event: MetricsEvent) -> None:
        pass

    def flush(self, run_id: str) -> None:
        pass

    def verbose_summary(self) -> dict | None:
        return None


class CapturingMetricsCollector:
    """Test double — accumulates events in memory."""

    def __init__(self) -> None:
        self.events: list[MetricsEvent] = []

    def record(self, event: MetricsEvent) -> None:
        self.events.append(event)

    def flush(self, run_id: str) -> None:
        pass

    def verbose_summary(self) -> dict | None:
        return None

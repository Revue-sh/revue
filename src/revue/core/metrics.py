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


@dataclass
class RoutingMetricsData:
    """Routing observability data captured after Cleo routing (REVUE-170 AC5)."""

    ai_suggested_agents: list[str]
    algorithm_selected_agents: list[str]
    final_agents: list[str]
    routing_source: str  # "ai_assisted" or "algorithm_fallback"
    model_used: str


@dataclass
class SynthesisMetricsData:
    """Synthesis observability data from Nova consolidation (REVUE-179 AC4)."""

    total_findings: int
    synthesised_count: int
    synthesis_events: list[dict] = field(default_factory=list)


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

    def record_routing(self, data: RoutingMetricsData) -> None:
        """Record routing observability data for the current run."""
        ...

    def record_synthesis(self, data: SynthesisMetricsData) -> None:
        """Record synthesis observability data for the current run."""
        ...


class NullMetricsCollector:
    """Default no-op metrics collector (used when metrics disabled)."""

    def record(self, event: MetricsEvent) -> None:
        pass

    def flush(self, run_id: str) -> None:
        pass

    def verbose_summary(self) -> dict | None:
        return None

    def record_routing(self, data: RoutingMetricsData) -> None:
        pass

    def record_synthesis(self, data: SynthesisMetricsData) -> None:
        pass


class CapturingMetricsCollector:
    """Test double — accumulates events in memory."""

    def __init__(self) -> None:
        self.events: list[MetricsEvent] = []
        self.routing_events: list[RoutingMetricsData] = []
        self.synthesis_events: list[SynthesisMetricsData] = []

    def record(self, event: MetricsEvent) -> None:
        self.events.append(event)

    def flush(self, run_id: str) -> None:
        pass

    def verbose_summary(self) -> dict | None:
        return None

    def record_routing(self, data: RoutingMetricsData) -> None:
        self.routing_events.append(data)

    def record_synthesis(self, data: SynthesisMetricsData) -> None:
        self.synthesis_events.append(data)

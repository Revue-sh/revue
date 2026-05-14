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


@dataclass
class RunVerdictMetricsData:
    """REVUE-246 AC7: per-run verdict tallies.

    ``verdict`` is the four-state run-level outcome (clean / findings /
    degraded / failed) and the count fields surface how many agents landed
    in each terminal state. ``errors_by_code`` splits the error count by
    its closed-set code so operators can tell schema mismatches apart from
    refusals apart from iteration exhaustion when triaging.
    """

    verdict: str
    clean_count: int
    finding_count: int
    error_count: int
    errors_by_code: dict[str, int] = field(default_factory=dict)


@dataclass
class VexMetricsData:
    """Vex verification observability — verdict and failure tallies for one run.

    verdict_counts keys: apply, drop_cr_keep_prose, reject_finding.
    failure_counts keys: no_code_replacement, read_error, verifier_exception.
    Persisted alongside agent token usage so the cost and outcome of semantic
    verification can be audited without parsing terminal output (REVUE-241).
    """

    verdict_counts: dict[str, int] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)


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

    def record_vex(self, data: VexMetricsData) -> None:
        """Record Vex verdict/failure tallies for the current run."""
        ...

    def record_run_verdict(self, data: RunVerdictMetricsData) -> None:
        """Record the run-level four-state verdict (REVUE-246 AC7)."""
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

    def record_vex(self, data: VexMetricsData) -> None:
        pass

    def record_run_verdict(self, data: RunVerdictMetricsData) -> None:
        pass


class CapturingMetricsCollector:
    """Test double — accumulates events in memory."""

    def __init__(self) -> None:
        self.events: list[MetricsEvent] = []
        self.routing_events: list[RoutingMetricsData] = []
        self.synthesis_events: list[SynthesisMetricsData] = []
        self.vex_events: list[VexMetricsData] = []
        self.run_verdict_events: list[RunVerdictMetricsData] = []

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

    def record_vex(self, data: VexMetricsData) -> None:
        self.vex_events.append(data)

    def record_run_verdict(self, data: RunVerdictMetricsData) -> None:
        self.run_verdict_events.append(data)

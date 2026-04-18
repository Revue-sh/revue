#!/usr/bin/env python3
"""Tests for MetricsCollector Protocol + implementations — REVUE-154."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# T1 — MetricsEvent dataclass
# ---------------------------------------------------------------------------

def test_metrics_event_dataclass() -> None:
    from revue.core.metrics import MetricsEvent
    event = MetricsEvent(
        event_type="agent_call",
        timestamp="2026-04-18T10:00:00Z",
        agent_name="zara",
        provider="anthropic",
        model="claude-sonnet-4-6",
        cache_creation_tokens=1000,
        cache_read_tokens=500,
        input_tokens=1500,
        output_tokens=200,
    )
    assert event.event_type == "agent_call"
    assert event.agent_name == "zara"
    assert event.cache_creation_tokens == 1000
    assert event.cache_read_tokens == 500
    assert event.input_tokens == 1500
    assert event.output_tokens == 200


# ---------------------------------------------------------------------------
# T1 — NullMetricsCollector
# ---------------------------------------------------------------------------

def test_null_collector_no_op() -> None:
    from revue.core.metrics import MetricsEvent, NullMetricsCollector
    collector = NullMetricsCollector()
    event = MetricsEvent(
        event_type="agent_call",
        timestamp="2026-04-18T10:00:00Z",
        agent_name=None,
        provider="anthropic",
        model="claude-sonnet-4-6",
        cache_creation_tokens=0,
        cache_read_tokens=0,
        input_tokens=100,
        output_tokens=50,
    )
    collector.record(event)
    collector.flush("run-123")


# ---------------------------------------------------------------------------
# T1 — CapturingMetricsCollector
# ---------------------------------------------------------------------------

def test_capturing_collector_records_events() -> None:
    from revue.core.metrics import CapturingMetricsCollector, MetricsEvent

    collector = CapturingMetricsCollector()
    assert collector.events == []

    e1 = MetricsEvent(
        event_type="agent_call",
        timestamp="2026-04-18T10:00:00Z",
        agent_name="zara",
        provider="anthropic",
        model="claude-sonnet-4-6",
        cache_creation_tokens=1000,
        cache_read_tokens=0,
        input_tokens=1000,
        output_tokens=200,
    )
    e2 = MetricsEvent(
        event_type="agent_call",
        timestamp="2026-04-18T10:00:01Z",
        agent_name="kai",
        provider="anthropic",
        model="claude-sonnet-4-6",
        cache_creation_tokens=0,
        cache_read_tokens=1000,
        input_tokens=1000,
        output_tokens=180,
    )

    collector.record(e1)
    collector.record(e2)

    assert len(collector.events) == 2
    assert collector.events[0].agent_name == "zara"
    assert collector.events[1].agent_name == "kai"
    assert collector.events[0].cache_creation_tokens == 1000
    assert collector.events[1].cache_read_tokens == 1000


# ---------------------------------------------------------------------------
# verbose_summary() — Protocol compliance
# ---------------------------------------------------------------------------

def test_null_collector_verbose_summary_returns_none() -> None:
    from revue.core.metrics import NullMetricsCollector
    assert NullMetricsCollector().verbose_summary() is None


def test_capturing_collector_verbose_summary_returns_none() -> None:
    from revue.core.metrics import CapturingMetricsCollector
    assert CapturingMetricsCollector().verbose_summary() is None

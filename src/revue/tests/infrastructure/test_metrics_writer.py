#!/usr/bin/env python3
"""Tests for JsonlMetricsCollector — REVUE-154."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# T2 — JsonlMetricsCollector basic operations
# ---------------------------------------------------------------------------

def test_jsonl_writer_appends_on_flush() -> None:
    """flush() writes valid JSON line; second flush() appends (not overwrites)."""
    from revue.core.metrics import MetricsEvent
    from revue.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)

        # First event and flush
        e1 = MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-18T10:00:00Z",
            agent_name="zara",
            provider="anthropic",
            model="claude-sonnet-4-6",
            cache_creation_tokens=100,
            cache_read_tokens=0,
            input_tokens=100,
            output_tokens=50,
        )
        collector.record(e1)
        collector.flush("run-001")

        # Verify first line exists and is valid JSON
        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        assert metrics_file.exists()
        lines = metrics_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data1 = json.loads(lines[0])
        assert data1["run_id"] == "run-001"

        # Second event and flush
        e2 = MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-18T10:00:01Z",
            agent_name="kai",
            provider="anthropic",
            model="claude-sonnet-4-6",
            cache_creation_tokens=0,
            cache_read_tokens=100,
            input_tokens=100,
            output_tokens=40,
        )
        collector2 = JsonlMetricsCollector(base_dir=tmpdir)  # New collector instance
        collector2.record(e2)
        collector2.flush("run-002")

        # Verify second line appended (not overwritten)
        lines = metrics_file.read_text().strip().split("\n")
        assert len(lines) == 2
        data2 = json.loads(lines[1])
        assert data2["run_id"] == "run-002"


# ---------------------------------------------------------------------------
# T2 — JsonlMetricsCollector event aggregation
# ---------------------------------------------------------------------------

def test_jsonl_writer_aggregates_events() -> None:
    """flush() aggregates events by agent and computes totals."""
    from revue.core.metrics import MetricsEvent
    from revue.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)

        # Simulate a run with events from 2 agents
        events = [
            MetricsEvent(
                event_type="agent_call",
                timestamp="2026-04-18T10:00:00Z",
                agent_name="zara",
                provider="anthropic",
                model="claude-sonnet-4-6",
                cache_creation_tokens=1000,
                cache_read_tokens=0,
                input_tokens=1000,
                output_tokens=200,
            ),
            MetricsEvent(
                event_type="agent_call",
                timestamp="2026-04-18T10:00:01Z",
                agent_name="kai",
                provider="anthropic",
                model="claude-sonnet-4-6",
                cache_creation_tokens=0,
                cache_read_tokens=1000,
                input_tokens=1000,
                output_tokens=180,
            ),
        ]

        for event in events:
            collector.record(event)

        collector.flush("run-test")

        # Parse and validate JSON structure
        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert data["run_id"] == "run-test"
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-sonnet-4-6"

        # Verify agents array
        assert "agents" in data
        agents_by_name = {a["name"]: a for a in data["agents"]}
        assert "zara" in agents_by_name
        assert "kai" in agents_by_name
        assert agents_by_name["zara"]["cache_creation_tokens"] == 1000
        assert agents_by_name["kai"]["cache_read_tokens"] == 1000

        # Verify totals
        assert "totals" in data
        assert data["totals"]["cache_creation_tokens"] == 1000
        assert data["totals"]["cache_read_tokens"] == 1000
        assert data["totals"]["input_tokens"] == 2000
        assert data["totals"]["output_tokens"] == 380


# ---------------------------------------------------------------------------
# verbose_summary() — in-memory totals
# ---------------------------------------------------------------------------

def test_verbose_summary_none_before_flush() -> None:
    from revue.infrastructure.metrics_writer import JsonlMetricsCollector
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        assert collector.verbose_summary() is None


def test_verbose_summary_returns_totals_after_flush() -> None:
    from revue.core.metrics import MetricsEvent
    from revue.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-18T10:00:00Z",
            agent_name="zara",
            provider="anthropic",
            model="claude-sonnet-4-6",
            cache_creation_tokens=500,
            cache_read_tokens=1500,
            input_tokens=2000,
            output_tokens=300,
        ))
        collector.flush("run-verbose")

        summary = collector.verbose_summary()
        assert summary is not None
        assert summary["cache_creation_tokens"] == 500
        assert summary["cache_read_tokens"] == 1500
        assert summary["input_tokens"] == 2000
        assert summary["output_tokens"] == 300

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
    from revue_core.core.metrics import MetricsEvent
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

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
    from revue_core.core.metrics import MetricsEvent
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

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
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        assert collector.verbose_summary() is None


def test_verbose_summary_returns_totals_after_flush() -> None:
    from revue_core.core.metrics import MetricsEvent
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

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


# ---------------------------------------------------------------------------
# REVUE-170: AC5 — routing metrics in flush record
# ---------------------------------------------------------------------------

def test_ac5_routing_data_all_fields_in_flush_record() -> None:
    """AC5: flush() includes a 'routing' key with all five required fields when
    record_routing() has been called. Every field is asserted by name."""
    from revue_core.core.metrics import MetricsEvent, RoutingMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-23T10:00:00Z",
            agent_name="zara",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
        ))
        collector.record_routing(RoutingMetricsData(
            ai_suggested_agents=["zara", "kai"],
            algorithm_selected_agents=["zara", "kai", "maya", "leo"],
            final_agents=["zara", "kai"],
            routing_source="ai_assisted",
            model_used="claude-sonnet-4-6",
        ))
        collector.flush("run-routing")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert "routing" in data, "flush record must contain 'routing' key"
        r = data["routing"]
        # AC5: assert every field by name
        assert r["ai_suggested_agents"] == ["zara", "kai"]
        assert r["algorithm_selected_agents"] == ["zara", "kai", "maya", "leo"]
        assert r["final_agents"] == ["zara", "kai"]
        assert r["routing_source"] == "ai_assisted"
        assert r["model_used"] == "claude-sonnet-4-6"


def test_ac5_no_routing_data_flush_has_no_routing_key() -> None:
    """When record_routing() is not called, flush record has no 'routing' key."""
    from revue_core.core.metrics import MetricsEvent
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-23T10:00:00Z",
            agent_name="zara",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
        ))
        collector.flush("run-no-routing")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert "routing" not in data


def test_p3_routing_cleared_when_flush_exits_early_due_to_no_events() -> None:
    """P3: _routing is reset even when flush() exits early (no events).
    A subsequent flush with events must not inherit stale routing data."""
    from revue_core.core.metrics import MetricsEvent, RoutingMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)

        # Run 1: record routing but no events → flush exits early
        collector.record_routing(RoutingMetricsData(
            ai_suggested_agents=["zara"],
            algorithm_selected_agents=["zara", "kai"],
            final_agents=["zara"],
            routing_source="ai_assisted",
            model_used="claude-sonnet-4-6",
        ))
        collector.flush("run-no-events")  # early exit — routing should be cleared

        # Run 2: events but no record_routing() call
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-23T10:00:00Z",
            agent_name="kai",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=50,
            output_tokens=25,
        ))
        collector.flush("run-with-events")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        records = [json.loads(line) for line in metrics_file.read_text().strip().splitlines()]

        # Only run-with-events was written (run-no-events had no events)
        assert len(records) == 1
        assert records[0]["run_id"] == "run-with-events"
        # Stale routing from run 1 must NOT bleed into run 2
        assert "routing" not in records[0], (
            "Stale _routing from a previous flush (with no events) must not persist to the next run"
        )


# ---------------------------------------------------------------------------
# REVUE-179: AC4 — synthesis events in flush record
# ---------------------------------------------------------------------------

def test_synthesis_events_in_flush_record() -> None:
    """AC4: flush() includes a 'findings' key with synthesis event data when
    record_synthesis() has been called. Every field is asserted by name."""
    from revue_core.core.metrics import MetricsEvent, SynthesisMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-27T10:00:00Z",
            agent_name="nova",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=200,
            output_tokens=80,
        ))
        collector.record_synthesis(SynthesisMetricsData(
            total_findings=4,
            synthesised_count=2,
            synthesis_events=[
                {
                    "from_agents": ["kai", "zara"],
                    "file": "src/api/users.py",
                    "line": 47,
                    "severity_in": ["high", "critical"],
                    "severity_out": "critical",
                },
                {
                    "from_agents": ["maya", "leo"],
                    "file": "src/models/order.py",
                    "line": 12,
                    "severity_in": ["medium", "high"],
                    "severity_out": "high",
                },
            ],
        ))
        collector.flush("run-synthesis")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert "findings" in data, "flush record must contain 'findings' key"
        f = data["findings"]
        assert f["total"] == 4
        assert f["synthesised"] == 2
        assert len(f["synthesis_events"]) == 2

        e0 = f["synthesis_events"][0]
        assert e0["from_agents"] == ["kai", "zara"]
        assert e0["file"] == "src/api/users.py"
        assert e0["line"] == 47
        assert e0["severity_in"] == ["high", "critical"]
        assert e0["severity_out"] == "critical"


def test_no_synthesis_data_flush_has_no_findings_key() -> None:
    """When record_synthesis() is not called, flush record has no 'findings' key."""
    from revue_core.core.metrics import MetricsEvent
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-04-27T10:00:00Z",
            agent_name="zara",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
        ))
        collector.flush("run-no-synthesis")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert "findings" not in data


# ---------------------------------------------------------------------------
# REVUE-241 — Vex verdict / failure counts in flush record
# ---------------------------------------------------------------------------

def test_vex_counts_in_flush_record() -> None:
    """flush() includes a 'vex' key with verdict_counts and failure_counts when
    record_vex() has been called. Surfaces Vex observability into the persisted
    metrics so the run cost of verification (and which findings were rejected)
    can be audited without parsing terminal output."""
    from revue_core.core.metrics import MetricsEvent, VexMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-05-12T10:00:00Z",
            agent_name="vex",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=400,
            output_tokens=120,
        ))
        collector.record_vex(VexMetricsData(
            verdict_counts={"apply": 1, "drop_cr_keep_prose": 0, "reject_finding": 7},
            failure_counts={"no_code_replacement": 15, "read_error": 0, "verifier_exception": 0},
        ))
        collector.flush("run-vex")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert "vex" in data, "flush record must contain 'vex' key"
        vex = data["vex"]
        assert vex["verdict_counts"] == {
            "apply": 1, "drop_cr_keep_prose": 0, "reject_finding": 7,
        }
        assert vex["failure_counts"] == {
            "no_code_replacement": 15, "read_error": 0, "verifier_exception": 0,
        }
        # REVUE-249 §D4 — guard_downgrade defaults to 0 when not supplied; it
        # is always present in the persisted record so a single jq filter can
        # parse it without an existence check.
        assert vex["guard_downgrade"] == 0


def test_record_vex_persists_orphan_guard_downgrade_counter() -> None:
    """REVUE-249 AC13 — when ``VexMetricsData.guard_downgrade`` is non-zero,
    the persisted metrics record carries it through. The counter is
    independent of verdict_counts / failure_counts so a guard-only run still
    shows up in metrics.jsonl.
    """
    import json
    import tempfile
    from pathlib import Path

    from revue_core.core.metrics import MetricsEvent, VexMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-05-15T10:00:00Z",
            agent_name="vex",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=200,
            output_tokens=80,
        ))
        collector.record_vex(VexMetricsData(
            verdict_counts={"apply": 2, "drop_cr_keep_prose": 0, "reject_finding": 0},
            failure_counts={},
            guard_downgrade=3,
        ))
        collector.flush("run-guard")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert data["vex"]["guard_downgrade"] == 3


def test_no_vex_data_flush_has_no_vex_key() -> None:
    """When record_vex() is not called, flush record has no 'vex' key."""
    from revue_core.core.metrics import MetricsEvent
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call",
            timestamp="2026-05-12T10:00:00Z",
            agent_name="maya",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
        ))
        collector.flush("run-no-vex")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        data = json.loads(metrics_file.read_text().strip())

        assert "vex" not in data


def test_vex_cleared_after_flush() -> None:
    """Stale Vex data must not bleed into the next run's flush record."""
    from revue_core.core.metrics import MetricsEvent, VexMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call", timestamp="2026-05-12T10:00:00Z",
            agent_name="vex", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=400, output_tokens=120,
        ))
        collector.record_vex(VexMetricsData(
            verdict_counts={"apply": 2, "drop_cr_keep_prose": 1, "reject_finding": 3},
            failure_counts={"no_code_replacement": 5, "read_error": 1, "verifier_exception": 0},
        ))
        collector.flush("run-1")

        # Second run — no record_vex this time
        collector.record(MetricsEvent(
            event_type="agent_call", timestamp="2026-05-12T11:00:00Z",
            agent_name="maya", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=200, output_tokens=80,
        ))
        collector.flush("run-2")

        metrics_file = Path(tmpdir) / ".revue" / "metrics.jsonl"
        lines = metrics_file.read_text().strip().splitlines()
        assert len(lines) == 2
        run2 = json.loads(lines[1])
        assert "vex" not in run2, "stale Vex data must clear between runs"


# ---------------------------------------------------------------------------
# REVUE-246 AC7 — run-level verdict + per-status counts in metrics.jsonl
# ---------------------------------------------------------------------------


def test_jsonl_writer_persists_run_verdict_and_counts() -> None:
    """AC7: each run record must carry the four-state verdict plus
    ``clean_count`` / ``finding_count`` / ``error_count`` fields, with errors
    further split out by code so operators can grep for runs that bailed
    on a specific failure mode."""
    # Arrange
    from revue_core.core.metrics import MetricsEvent, RunVerdictMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call", timestamp="2026-05-13T12:00:00Z",
            agent_name="maya", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=100, output_tokens=20,
        ))
        collector.record_run_verdict(RunVerdictMetricsData(
            verdict="degraded",
            clean_count=1, finding_count=1, error_count=2,
            errors_by_code={"invalid_response_schema": 2},
        ))

        # Act
        collector.flush("run-1")

        # Assert — run_verdict block is persisted with all counts intact
        line = (Path(tmpdir) / ".revue" / "metrics.jsonl").read_text().strip()
        record = json.loads(line)
        assert record["run_verdict"]["verdict"] == "degraded"
        assert record["run_verdict"]["clean_count"] == 1
        assert record["run_verdict"]["finding_count"] == 1
        assert record["run_verdict"]["error_count"] == 2
        assert record["run_verdict"]["errors_by_code"] == {
            "invalid_response_schema": 2
        }


def test_jsonl_writer_run_verdict_clears_between_runs() -> None:
    """Like the existing routing / synthesis / vex fields, run_verdict is
    one-shot — a second run that doesn't call record_run_verdict must not
    inherit the previous run's verdict."""
    # Arrange
    from revue_core.core.metrics import MetricsEvent, RunVerdictMetricsData
    from revue_core.infrastructure.metrics_writer import JsonlMetricsCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = JsonlMetricsCollector(base_dir=tmpdir)
        collector.record(MetricsEvent(
            event_type="agent_call", timestamp="2026-05-13T12:00:00Z",
            agent_name="leo", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=10, output_tokens=2,
        ))
        collector.record_run_verdict(RunVerdictMetricsData(
            verdict="clean", clean_count=4, finding_count=0, error_count=0,
        ))
        collector.flush("run-1")

        # Act — second run records an event but does NOT call record_run_verdict
        collector.record(MetricsEvent(
            event_type="agent_call", timestamp="2026-05-13T12:05:00Z",
            agent_name="leo", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=10, output_tokens=2,
        ))
        collector.flush("run-2")

        # Assert — stale verdict from run-1 must not bleed into run-2
        lines = (Path(tmpdir) / ".revue" / "metrics.jsonl").read_text().strip().splitlines()
        run2 = json.loads(lines[1])
        assert "run_verdict" not in run2, (
            "stale run_verdict must clear between runs (one-shot field)"
        )

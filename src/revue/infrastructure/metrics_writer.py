#!/usr/bin/env python3
"""JSONL metrics writer — REVUE-154."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from revue.core.metrics import MetricsCollector, MetricsEvent


class JsonlMetricsCollector(MetricsCollector):
    """Writes per-run metrics to .revue/metrics.jsonl as JSONL format."""

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir)
        self.events: list[MetricsEvent] = []
        self._last_totals: dict | None = None

    def record(self, event: MetricsEvent) -> None:
        """Accumulate an event in memory."""
        self.events.append(event)

    def flush(self, run_id: str) -> None:
        """Write accumulated events to .revue/metrics.jsonl as a single JSON object."""
        if not self.events:
            return

        metrics_dir = self.base_dir / ".revue"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_file = metrics_dir / "metrics.jsonl"

        # Aggregate events by agent
        agent_data: dict[str | None, dict[str, Any]] = defaultdict(
            lambda: {
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        totals: dict[str, int] = {
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

        provider = None
        model = None

        for event in self.events:
            # Track provider and model from first event
            if provider is None:
                provider = event.provider
            if model is None:
                model = event.model

            # Aggregate by agent
            agent_key = event.agent_name or "__default__"
            agent_data[agent_key]["cache_creation_tokens"] += event.cache_creation_tokens
            agent_data[agent_key]["cache_read_tokens"] += event.cache_read_tokens
            agent_data[agent_key]["input_tokens"] += event.input_tokens
            agent_data[agent_key]["output_tokens"] += event.output_tokens

            # Accumulate totals
            totals["cache_creation_tokens"] += event.cache_creation_tokens
            totals["cache_read_tokens"] += event.cache_read_tokens
            totals["input_tokens"] += event.input_tokens
            totals["output_tokens"] += event.output_tokens

        # Build agents array with per-agent data
        agents = []
        for agent_name, data in sorted(agent_data.items()):
            if agent_name == "__default__":
                agent_name = None
            agent_entry: dict[str, Any] = {
                "cache_creation_tokens": data["cache_creation_tokens"],
                "cache_read_tokens": data["cache_read_tokens"],
                "input_tokens": data["input_tokens"],
                "output_tokens": data["output_tokens"],
            }
            if agent_name is not None:
                agent_entry["name"] = agent_name
            agents.append(agent_entry)

        # Construct the run record
        run_record = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider or "unknown",
            "model": model or "unknown",
            "agents": agents,
            "totals": totals,
        }

        # Store totals in memory before clearing (for verbose_summary())
        self._last_totals = dict(totals)

        # Append as a single JSON line
        with open(metrics_file, "a") as f:
            f.write(json.dumps(run_record) + "\n")

        # Clear events for next run
        self.events = []

    def verbose_summary(self) -> dict | None:
        """Return in-memory totals from the last flush, or None if flush never called."""
        return self._last_totals

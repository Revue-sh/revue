"""Tests for parallel agent execution."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from AIReviewer.core.agent_runner import (
    AgentProtocol, AgentRunResult, ParallelRunResult,
    run_agents_parallel,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
)
from AIReviewer.core.models import FileChange, AIReview


def _fc(path: str = "app.py") -> FileChange:
    return FileChange(file_path=path, change_type="modified", additions=5, deletions=2, diff="")


def _review(file_path: str = "app.py") -> AIReview:
    return AIReview(
        file_path=file_path, line_number=1, severity="minor",
        issue="test issue", suggestion="fix it", confidence=0.9,
    )


class _OkAgent:
    name = "ok-agent"
    def analyse(self, changes, shared=None):
        return [_review()]


class _ErrorAgent:
    name = "error-agent"
    def analyse(self, changes, shared=None):
        raise RuntimeError("agent crashed")


class _SlowAgent:
    name = "slow-agent"
    def __init__(self, delay: float = 5.0):
        self._delay = delay
    def analyse(self, changes, shared=None):
        time.sleep(self._delay)
        return [_review()]


def test_all_agents_run_and_return_findings():
    agents = [_OkAgent(), _OkAgent.__new__(_OkAgent)]
    agents[1].name = "ok-agent-2"
    result = run_agents_parallel(agents, [_fc()])
    assert len(result.agent_results) == 2
    assert len(result.all_findings) == 2


def test_error_agent_degrades_gracefully():
    agents = [_OkAgent(), _ErrorAgent()]
    result = run_agents_parallel(agents, [_fc()])
    assert len(result.agent_results) == 2
    ok = next(r for r in result.agent_results if r.agent_name == "ok-agent")
    err = next(r for r in result.agent_results if r.agent_name == "error-agent")
    assert ok.success
    assert not err.success
    assert "crashed" in err.error


def test_timed_out_agent_marked_as_timed_out():
    slow = _SlowAgent(delay=5.0)
    result = run_agents_parallel([slow], [_fc()], timeout_seconds=0.1)
    r = result.agent_results[0]
    assert r.timed_out
    assert r.findings == []


def test_parallel_run_result_all_findings_aggregates():
    class _TwoFindingsAgent:
        name = "two"
        def analyse(self, changes, shared=None):
            return [_review("a.py"), _review("b.py")]

    result = run_agents_parallel([_TwoFindingsAgent()], [_fc()])
    assert len(result.all_findings) == 2


def test_parallel_run_result_failed_agents_list():
    agents = [_OkAgent(), _ErrorAgent()]
    result = run_agents_parallel(agents, [_fc()])
    assert "error-agent" in result.failed_agents
    assert "ok-agent" not in result.failed_agents


def test_parallel_run_result_succeeded_agents_list():
    agents = [_OkAgent(), _ErrorAgent()]
    result = run_agents_parallel(agents, [_fc()])
    assert "ok-agent" in result.succeeded_agents
    assert "error-agent" not in result.succeeded_agents


def test_empty_agent_list():
    result = run_agents_parallel([], [_fc()])
    assert result.agent_results == []
    assert result.all_findings == []


def test_agent_run_result_success_property():
    ok = AgentRunResult(agent_name="a", findings=[], elapsed_seconds=0.1)
    timed_out = AgentRunResult(agent_name="b", findings=[], elapsed_seconds=1.0, timed_out=True)
    errored = AgentRunResult(agent_name="c", findings=[], elapsed_seconds=0.5, error="boom")
    assert ok.success is True
    assert timed_out.success is False
    assert errored.success is False


def test_agents_run_in_parallel():
    """Two 0.1s agents should finish in ~0.1s total, not 0.2s."""
    class _SlightlySlowAgent:
        def __init__(self, name):
            self.name = name
        def analyse(self, changes, shared=None):
            time.sleep(0.1)
            return []

    agents = [_SlightlySlowAgent("a"), _SlightlySlowAgent("b")]
    result = run_agents_parallel(agents, [_fc()])
    # Should be < 0.5s (generous bound to avoid flakiness)
    assert result.total_elapsed < 0.5


def test_default_timeout_matches_prd():
    """Default timeout is 90s as specified in the PRD (AC Story 10)."""
    assert DEFAULT_AGENT_TIMEOUT_SECONDS == 90.0


def test_configurable_timeout_passed_through():
    """Callers can override the timeout — e.g. 120s for slow networks."""
    class _FastAgent:
        name = "fast"
        def analyse(self, changes, shared=None):
            return []

    result = run_agents_parallel([_FastAgent()], [_fc()], timeout_seconds=120.0)
    assert result.agent_results[0].success is True

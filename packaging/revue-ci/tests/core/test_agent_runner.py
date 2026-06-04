"""Tests for parallel agent execution."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from revue_core.core.agent_runner import (
    AgentProtocol, AgentRunResult, ParallelRunResult,
    run_agents_parallel,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
)
from revue_core.core.models import FileChange, AIReview


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


# ---------------------------------------------------------------------------
# REVUE-241: per-agent failure attribution
#
# When an agent fails, the existing report shows only the agent name and a
# truncated message. Operators investigating a CI failure (e.g. Maya + Kai
# blowing the 200K context window) need to know:
#   1. The Python exception class (RuntimeError vs APIStatusError vs …)
#   2. Which client method raised it (complete vs complete_with_tools)
# AgentRunResult now exposes those as explicit fields so the pipeline and CLI
# can surface them in the "Review incomplete" summary without re-parsing free
# text from log lines.
# ---------------------------------------------------------------------------


def test_error_agent_captures_exception_type():
    """`error_type` is the unqualified class name of the raised exception.

    A bare `str(exc)` collapses ValueError("x") and RuntimeError("x") into the
    same surface — operators can't distinguish a config bug from a transport
    fault. The exception class is the first triage signal.
    """
    agents = [_ErrorAgent()]
    result = run_agents_parallel(agents, [_fc()])
    err = result.agent_results[0]
    assert err.error_type == "RuntimeError"
    assert err.error == "agent crashed"


def test_error_agent_captures_call_site_attribute_when_set():
    """Agents may attach a ``call_site`` attribute to their exception to name
    the client method that raised. The runner reads it without coupling
    AgentProtocol to any specific exception class — any duck-typed object
    with ``.call_site`` works."""
    class _CallSiteAgent:
        name = "with-call-site"
        def analyse(self, changes, shared=None):
            exc = RuntimeError("prompt is too long")
            exc.call_site = "AnthropicClient.complete_with_tools"  # type: ignore[attr-defined]
            raise exc

    result = run_agents_parallel([_CallSiteAgent()], [_fc()])
    err = result.agent_results[0]
    assert err.error_type == "RuntimeError"
    assert err.call_site == "AnthropicClient.complete_with_tools"


def test_error_agent_without_call_site_has_empty_call_site():
    """Agents that don't attach a call_site leave the field empty — the
    field is informational, never a hard requirement."""
    agents = [_ErrorAgent()]
    result = run_agents_parallel(agents, [_fc()])
    err = result.agent_results[0]
    assert err.call_site == ""


def test_timed_out_agent_has_empty_error_type_and_call_site():
    """Timeouts are a distinct failure mode (no exception was raised inside
    the agent's body — the future was cancelled). Don't conflate them with
    exception-bearing failures."""
    slow = _SlowAgent(delay=5.0)
    result = run_agents_parallel([slow], [_fc()], timeout_seconds=0.1)
    r = result.agent_results[0]
    assert r.timed_out
    assert r.error_type == ""
    assert r.call_site == ""


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


# ---------------------------------------------------------------------------
# REVUE-339: cooperative deadline + finalize budget reservation.
#
# run_agents_parallel computes a single global deadline ONCE before submitting
# futures and forwards it to every agent whose analyse() accepts a ``deadline``
# keyword. The deadline is shared across all concurrent agents (NOT per-agent —
# see REVUE-320). Legacy agents whose analyse() does not accept ``deadline``
# must keep working unchanged.
# ---------------------------------------------------------------------------

from revue_core.core.agent_runner import DEFAULT_FINALIZE_RESERVE_SECONDS  # noqa: E402


def test_default_finalize_reserve_matches_empirical_thirty_seconds():
    """AC5: finalize_reserve defaults to 30s."""
    assert DEFAULT_FINALIZE_RESERVE_SECONDS == 30.0


def test_deadline_forwarded_to_agents_that_accept_it():
    """AC1: a global deadline is computed once and passed to each agent's
    analyse() when that agent declares a ``deadline`` parameter."""
    captured: dict[str, float] = {}

    class _DeadlineAwareAgent:
        name = "deadline-aware"
        def analyse(self, changes, shared=None, deadline=None):
            captured["deadline"] = deadline
            return []

    before = time.monotonic()
    run_agents_parallel(
        [_DeadlineAwareAgent()], [_fc()],
        timeout_seconds=90.0,
    )
    after = time.monotonic()

    assert "deadline" in captured
    deadline = captured["deadline"]
    assert deadline is not None
    # Deadline ≈ start + timeout_seconds (raw wall-clock); the loop subtracts
    # finalize_reserve itself. Must land in the expected window.
    assert before + 90.0 <= deadline <= after + 90.0


def test_single_global_deadline_shared_across_concurrent_agents():
    """AC1: all concurrent agents must receive the SAME deadline value — it is
    computed once, not per-agent."""
    seen: list[float] = []
    import threading
    lock = threading.Lock()

    class _Recorder:
        def __init__(self, name: str):
            self.name = name
        def analyse(self, changes, shared=None, deadline=None):
            with lock:
                seen.append(deadline)
            return []

    agents = [_Recorder(f"agent-{i}") for i in range(3)]
    run_agents_parallel(agents, [_fc()], timeout_seconds=90.0)

    assert len(seen) == 3
    assert all(d is not None for d in seen)
    # Every agent saw the identical deadline float — one global value.
    assert len(set(seen)) == 1


def test_legacy_agent_without_deadline_param_still_runs():
    """AC1 / Liskov: agents whose analyse() predates the deadline param must
    keep working — run_agents_parallel must not pass deadline to them."""
    class _LegacyAgent:
        name = "legacy"
        def analyse(self, changes, shared=None):
            return [_review()]

    result = run_agents_parallel([_LegacyAgent()], [_fc()], timeout_seconds=90.0)
    assert result.agent_results[0].success is True
    assert len(result.all_findings) == 1


def test_finalize_reserve_is_configurable():
    """AC1/AC5: the reserve can be overridden by the caller; the default is 30s
    but a caller may tune it. The computed deadline is independent of reserve
    (reserve is subtracted inside the loop, not here)."""
    captured: dict[str, float] = {}

    class _DeadlineAwareAgent:
        name = "deadline-aware"
        def analyse(self, changes, shared=None, deadline=None):
            captured["deadline"] = deadline
            return []

    before = time.monotonic()
    run_agents_parallel(
        [_DeadlineAwareAgent()], [_fc()],
        timeout_seconds=90.0,
        finalize_reserve=45.0,
    )
    after = time.monotonic()

    # Deadline is raw wall-clock regardless of reserve value.
    assert before + 90.0 <= captured["deadline"] <= after + 90.0

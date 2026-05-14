"""Regression: Consolidator runs Pass B in parallel, not sequentially.

Serial Nova synthesis (one API call per group, in a for-loop) stalled CI
for many minutes on PRs with 30+ findings — each group makes a Nova call
that may chain up to ``DEFAULT_MAX_TOOL_ITERATIONS`` tool rounds via
read_file. Parallelising at the consolidator level is the cheapest
single change that recovers the wall time.

These tests pin the contract:
  * Pass B uses concurrent execution (wall time ≪ serial baseline).
  * Output ordering matches input group order (downstream sort assumes it).
  * Per-group exceptions are isolated and skipped, not fatal.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from revue.comments.consolidator import Consolidator, ProximityAndCountGroupingStrategy
from revue.comments.models import (
    AgentFinding,
    ConsolidatedFinding,
    Attribution,
    SynthesisGroup,
)


def _finding(file_path: str, line: int) -> AgentFinding:
    return AgentFinding(
        file_path=file_path,
        line_number=line,
        severity="medium",
        issue=f"issue at {file_path}:{line}",
        suggestion="do thing",
        confidence=0.8,
        category="code-quality",
        agent_name="maya",
        code_replacement=None,
        replacement_line_count=1,
        snippet="",
    )


def _consolidated_for(group: SynthesisGroup) -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path=group.file_path,
        line_number=group.line_range[0],
        severity="medium",
        issue=group.findings[0].issue,
        suggestion=group.findings[0].suggestion,
        confidence=0.8,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=None,
        replacement_line_count=1,
        snippet="",
    )


class _SleepyStrategy:
    """Each synthesise() sleeps to make serialism observable."""

    def __init__(self, delay_s: float = 0.05) -> None:
        self._delay = delay_s
        self.call_count = 0

    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding:
        self.call_count += 1
        time.sleep(self._delay)
        return _consolidated_for(group)


def test_pass_b_synthesis_runs_in_parallel():
    """With 8 groups each sleeping 100ms, total wall time must be far below 800ms."""
    delay_s = 0.1
    n_groups = 8
    # 8 distinct files so the proximity grouper emits 8 singleton groups.
    findings = [_finding(f"f{i}.py", 10) for i in range(n_groups)]

    strategy = _SleepyStrategy(delay_s=delay_s)
    consolidator = Consolidator(
        grouping=ProximityAndCountGroupingStrategy(),
        synthesis=strategy,
        max_synthesis_workers=8,
    )

    start = time.monotonic()
    out = consolidator.consolidate(findings)
    elapsed = time.monotonic() - start

    assert strategy.call_count == n_groups
    assert len(out) == n_groups
    # Serial baseline would be n_groups * delay = 800ms. Parallel must come in
    # under half that even with thread-pool overhead and GIL contention.
    assert elapsed < (n_groups * delay_s) / 2, (
        f"Consolidator Pass B appears serial: elapsed={elapsed:.3f}s "
        f"for {n_groups} groups × {delay_s}s each (serial baseline "
        f"{n_groups * delay_s:.3f}s)"
    )


def test_pass_b_uses_thread_pool_executor():
    """Wiring guard: Consolidator must dispatch synthesis via ThreadPoolExecutor.

    Catches refactors that accidentally fall back to a serial for-loop.
    """
    findings = [_finding(f"f{i}.py", 10) for i in range(3)]
    strategy = _SleepyStrategy(delay_s=0.0)
    consolidator = Consolidator(
        grouping=ProximityAndCountGroupingStrategy(),
        synthesis=strategy,
        max_synthesis_workers=4,
    )

    with patch("revue.comments.consolidator.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as spy:
        consolidator.consolidate(findings)

    assert spy.called, "Consolidator must dispatch Pass B through ThreadPoolExecutor"


def test_pass_b_preserves_input_order():
    """Output order must follow input group order (downstream sort assumes it)."""
    # Distinct files so the grouper emits singleton groups in alphabetical order.
    findings = [_finding(f"file_{c}.py", 10) for c in "abcdefgh"]
    consolidator = Consolidator(
        grouping=ProximityAndCountGroupingStrategy(),
        synthesis=_SleepyStrategy(delay_s=0.01),
        max_synthesis_workers=8,
    )

    out = consolidator.consolidate(findings)
    paths = [c.file_path for c in out]
    assert paths == sorted(paths), f"Order not preserved: {paths}"


class _FlakyStrategy:
    """Raises on the 3rd call; other calls succeed."""

    def __init__(self) -> None:
        self.call_count = 0

    def synthesise(self, group: SynthesisGroup) -> ConsolidatedFinding:
        self.call_count += 1
        if self.call_count == 3:
            raise RuntimeError("simulated nova failure")
        return _consolidated_for(group)


def test_pass_b_isolates_per_group_failures():
    """One group's failure must not poison the others."""
    findings = [_finding(f"f{i}.py", 10) for i in range(5)]
    consolidator = Consolidator(
        grouping=ProximityAndCountGroupingStrategy(),
        synthesis=_FlakyStrategy(),
        max_synthesis_workers=4,
    )
    out = consolidator.consolidate(findings)
    # 5 groups → 4 successful consolidations (one was raised on)
    assert len(out) == 4

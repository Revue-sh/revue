"""Tests for Nova's majority-vote line reconciler (REVUE-248 §D3).

When ≥ N-1 of N agents in a SynthesisGroup agree on a single line_number,
Nova uses the majority line as the consolidated finding's anchor — instead of
silently falling back to ``group.line_range[0]`` (which threw away the
per-agent agreement signal).

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from revue.comments.consolidator import (
    _majority_vote_line,
    NovaSingleShotStrategy,
)
from revue.comments.models import AgentFinding, SynthesisGroup


def _agent_finding(*, agent_name: str, line: int) -> AgentFinding:
    return AgentFinding(
        file_path="src/sample.py",
        line_number=line,
        severity="medium",
        issue="hardcoded secret",
        suggestion="move to env var",
        confidence=0.85,
        category="security",
        agent_name=agent_name,
        code_replacement=None,
        replacement_line_count=1,
    )


def _group(*findings: AgentFinding) -> SynthesisGroup:
    lines = [f.line_number for f in findings]
    return SynthesisGroup(
        findings=list(findings),
        file_path=findings[0].file_path,
        line_range=(min(lines), max(lines)),
        group_type="proximity" if len(findings) > 1 else "singleton",
    )


# ---------------------------------------------------------------------------
# _majority_vote_line helper — pure logic
# ---------------------------------------------------------------------------


def test_three_of_three_agreement_returns_majority_line() -> None:
    """All three agents on the same line → majority is that line."""
    # Arrange
    group = _group(
        _agent_finding(agent_name="maya", line=5),
        _agent_finding(agent_name="leo", line=5),
        _agent_finding(agent_name="zara", line=5),
    )

    # Act
    majority = _majority_vote_line(group)

    # Assert
    assert majority == 5


def test_two_of_three_agreement_returns_majority_line() -> None:
    """Two agents agree, one disagrees → use the majority (this is the REVUE-247 case)."""
    # Arrange — Maya off-by-one (4), Leo and Zara correct (5)
    group = _group(
        _agent_finding(agent_name="maya", line=4),
        _agent_finding(agent_name="leo", line=5),
        _agent_finding(agent_name="zara", line=5),
    )

    # Act
    majority = _majority_vote_line(group)

    # Assert — 2 ≥ N-1 = 2 → use 5
    assert majority == 5


def test_no_majority_returns_none() -> None:
    """3 different lines → no majority → caller falls back to group.line_range[0]."""
    # Arrange
    group = _group(
        _agent_finding(agent_name="maya", line=4),
        _agent_finding(agent_name="leo", line=5),
        _agent_finding(agent_name="zara", line=6),
    )

    # Act
    majority = _majority_vote_line(group)

    # Assert
    assert majority is None


def test_singleton_group_returns_its_only_line() -> None:
    """Singleton groups trivially have a 'majority' of 1 of 1 — return that line.

    This keeps the reconciler's contract uniform: it always returns the
    consolidated line for groups where everyone agrees.
    """
    # Arrange
    group = _group(_agent_finding(agent_name="maya", line=42))

    # Act
    majority = _majority_vote_line(group)

    # Assert
    assert majority == 42


def test_two_agent_group_unanimous_returns_majority() -> None:
    """N=2: N-1=1, so any 2 agents agreeing trivially clear the threshold."""
    # Arrange
    group = _group(
        _agent_finding(agent_name="maya", line=10),
        _agent_finding(agent_name="leo", line=10),
    )

    # Act
    majority = _majority_vote_line(group)

    # Assert
    assert majority == 10


def test_two_agent_group_split_returns_none() -> None:
    """N=2, split 1-1: not enough agreement, return None."""
    # Arrange
    group = _group(
        _agent_finding(agent_name="maya", line=10),
        _agent_finding(agent_name="leo", line=11),
    )

    # Act
    majority = _majority_vote_line(group)

    # Assert
    assert majority is None


# ---------------------------------------------------------------------------
# Integration with NovaSingleShotStrategy — synthesis uses majority line
# ---------------------------------------------------------------------------


def _completion(text: str):
    """Lightweight stub for ai_client.complete return value."""

    class _R:
        def __init__(self, text: str) -> None:
            self.text = text
            self.usage = None
            self.cache_meta = None

    return _R(text)


def test_synthesise_uses_majority_line_over_group_first_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REVUE-247 regression: Maya=4, Leo=5, Zara=5 → consolidated line is 5, not 4.

    Even when Nova's LLM output names a different line, the deterministic
    majority-vote takes precedence. This is the load-bearing guarantee for the
    REVUE-247 blank-line-precedes-finding bug.
    """
    # Arrange
    captured_infos: list[str] = []
    from revue.comments import consolidator as cons

    monkeypatch.setattr(
        cons.Log.nova,
        "info",
        lambda msg, *args, **kwargs: captured_infos.append(msg % args if args else msg),
    )

    group = _group(
        _agent_finding(agent_name="maya", line=4),
        _agent_finding(agent_name="leo", line=5),
        _agent_finding(agent_name="zara", line=5),
    )

    client = MagicMock()
    # Nova returns line=4 (would be the bug); reconciler must override.
    client.complete.return_value = _completion(
        '[{"file": "src/sample.py", "line": 4, "issue": "secret", '
        '"suggestion": "env var", "severity": "high"}]'
    )

    strategy = NovaSingleShotStrategy(ai_client=client)

    # Act
    consolidated = strategy.synthesise(group)

    # Assert — line corrected to 5 (the majority)
    assert consolidated.line_number == 5
    # Reconciliation logged on nova channel
    assert any(
        "[nova-reconcile]" in m and "src/sample.py" in m and "5" in m
        for m in captured_infos
    ), f"expected [nova-reconcile] info log, captured: {captured_infos}"


def test_synthesise_falls_back_to_first_line_when_no_majority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 distinct lines → no majority → use group.line_range[0] (current behaviour)."""
    # Arrange — no reconciliation should fire
    captured_infos: list[str] = []
    from revue.comments import consolidator as cons

    monkeypatch.setattr(
        cons.Log.nova,
        "info",
        lambda msg, *args, **kwargs: captured_infos.append(msg % args if args else msg),
    )

    group = _group(
        _agent_finding(agent_name="maya", line=4),
        _agent_finding(agent_name="leo", line=5),
        _agent_finding(agent_name="zara", line=6),
    )

    client = MagicMock()
    client.complete.return_value = _completion(
        '[{"file": "src/sample.py", "line": 4, "issue": "secret", '
        '"suggestion": "env var", "severity": "high"}]'
    )

    strategy = NovaSingleShotStrategy(ai_client=client)

    # Act
    consolidated = strategy.synthesise(group)

    # Assert — Nova's reported line (4) is used because no majority emerged.
    # _build_consolidated falls back to group.line_range[0]=4 when needed; here Nova
    # provided 4 so it stays at 4.
    assert consolidated.line_number == 4
    # No reconciliation log fired
    assert not any("[nova-reconcile]" in m for m in captured_infos)

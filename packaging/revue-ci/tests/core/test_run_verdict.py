"""REVUE-246 AC5 + AC6: run-level verdict from per-agent statuses.

The pipeline composes a run-level verdict from each agent's terminal state.
The contract is closed-set: ``clean`` / ``findings`` / ``degraded`` / ``failed``.

* clean    — every agent reported ``status: clean``.
* findings — at least one agent reported findings; the rest were clean. No
              errors at all. (Errors flip the run into ``degraded``/``failed``
              even when one agent did find things.)
* degraded — ≥ 50% of agents errored. The remainder may have produced
              findings or been clean; either way the verdict is unreliable.
* failed   — every agent errored.

The threshold is documented as somewhat arbitrary in the REVUE-246 spec
under "Contentious points"; pin it in tests so a future change is explicit.
"""
from __future__ import annotations

import pytest

from revue_core.core.run_verdict import AgentStatus, RunVerdict, compute_run_verdict


# ---------------------------------------------------------------------------
# AgentStatus helpers
# ---------------------------------------------------------------------------


def _clean(name: str) -> AgentStatus:
    return AgentStatus(agent_name=name, status="clean", error_code=None)


def _findings(name: str, count: int = 1) -> AgentStatus:
    return AgentStatus(agent_name=name, status="findings", finding_count=count)


def _error(name: str, code: str = "invalid_response_schema") -> AgentStatus:
    return AgentStatus(agent_name=name, status="error", error_code=code)


# ---------------------------------------------------------------------------
# AC6 — verdict logic
# ---------------------------------------------------------------------------


def test_all_clean_agents_produce_clean_verdict() -> None:
    # Arrange
    statuses = [_clean("maya"), _clean("leo"), _clean("kai"), _clean("zara")]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert
    assert verdict.verdict == "clean"
    assert verdict.clean_count == 4
    assert verdict.finding_count == 0
    assert verdict.error_count == 0


def test_one_findings_rest_clean_produces_findings_verdict() -> None:
    """A mixed clean+findings run with no errors → findings verdict.
    The clean agents must remain visible in the breakdown (AC5)."""
    # Arrange
    statuses = [
        _findings("maya", count=2),
        _clean("leo"),
        _clean("kai"),
        _clean("zara"),
    ]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert
    assert verdict.verdict == "findings"
    assert verdict.clean_count == 3
    assert verdict.finding_count == 1
    assert verdict.error_count == 0


def test_majority_errors_produces_degraded_verdict() -> None:
    """≥ 50% errors → degraded. The remaining agents may have been clean
    or had findings — either way the run is not trustworthy enough to
    declare a verdict on the agents that did complete."""
    # Arrange
    statuses = [
        _error("maya"),
        _error("leo"),
        _findings("kai"),
        _clean("zara"),
    ]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert
    assert verdict.verdict == "degraded"
    assert verdict.error_count == 2


def test_exactly_fifty_percent_errors_is_degraded() -> None:
    """The threshold is ≥ 50%, not > 50%. Two-of-four errored is degraded."""
    # Arrange
    statuses = [_error("maya"), _error("leo"), _findings("kai"), _clean("zara")]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert
    assert verdict.verdict == "degraded"


def test_minority_errors_does_not_degrade() -> None:
    """One-of-four errors stays a findings/clean verdict — operators see the
    one error in the breakdown but the run is still actionable."""
    # Arrange
    statuses = [
        _error("maya"),
        _findings("leo"),
        _clean("kai"),
        _clean("zara"),
    ]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert — findings + clean + 1 error (25% errors → not degraded)
    assert verdict.verdict == "findings"
    assert verdict.error_count == 1


def test_all_errors_produces_failed_verdict() -> None:
    """Every agent errored — nothing was actually reviewed. Failed."""
    # Arrange
    statuses = [_error("maya"), _error("leo"), _error("kai"), _error("zara")]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert
    assert verdict.verdict == "failed"


def test_empty_agent_list_is_failed() -> None:
    """A run with no agent results at all cannot be clean; treat it as
    failed so it doesn't masquerade as a positive review."""
    # Arrange — empty list

    # Act
    verdict = compute_run_verdict([])

    # Assert
    assert verdict.verdict == "failed"


# ---------------------------------------------------------------------------
# AC5 — per-agent breakdown is preserved
# ---------------------------------------------------------------------------


def test_breakdown_preserves_each_agent_in_input_order() -> None:
    """Per AC5: clean agents are NOT merged into the findings aggregate.
    The breakdown surfaces every agent with its status so the operator can
    see exactly which agents reviewed what."""
    # Arrange
    statuses = [
        _clean("maya"),
        _findings("leo", count=3),
        _error("kai", "model_refusal"),
        _clean("zara"),
    ]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert — each agent surfaces in its original position
    names = [a.agent_name for a in verdict.breakdown]
    assert names == ["maya", "leo", "kai", "zara"]


def test_errors_by_code_splits_failures_for_metrics_dashboard() -> None:
    """AC7 prep: metrics needs counts broken down by error code so
    operators can tell schema mismatches apart from refusals apart from
    iteration exhaustion. The verdict surfaces this directly."""
    # Arrange
    statuses = [
        _error("maya", "model_refusal"),
        _error("leo", "invalid_response_schema"),
        _error("kai", "invalid_response_schema"),
        _clean("zara"),
    ]

    # Act
    verdict = compute_run_verdict(statuses)

    # Assert
    assert verdict.errors_by_code == {
        "model_refusal": 1,
        "invalid_response_schema": 2,
    }

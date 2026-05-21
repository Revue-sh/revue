"""REVUE-324 — DeepSeek-focused matrix tests for Vex Option C.

Each test exercises one shape of the DeepSeek v4 Pro / OpenRouter response
that the verifier must handle correctly:

  1. Happy path: valid ``content`` + populated ``reasoning_details``
     → verdict parsed from content; counter at zero.
  2. Empty content + populated ``reasoning_details``
     → fail-open to ``apply``; ``reasoning_missing_count`` increments.
  3. Malformed ``reasoning_details`` (not a list) + valid content
     → verdict parsed normally; missing counter NOT touched (the malformed
     case is silently tolerated — we never mine reasoning for verdicts).
  4. Silently dropped param (no ``reasoning_details`` field at all) + empty content
     → behaviour identical to today's no-reasoning fail-open; counter at zero.

These are deliberately separate from ``test_vex_verifier.py`` so the
matrix can grow as new DeepSeek response shapes are observed in the wild
without touching the existing test surface.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from revue_core.comments._verifier import VexVerifier
from revue_core.comments.models import Attribution, ConsolidatedFinding
from revue_core.core.ai_client import CompletionResult, TokenUsage


def _finding() -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path="src/example.py",
        line_number=10,
        severity="medium",
        issue="missing input validation",
        suggestion="raise ValueError when input is None",
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=["    return value + 1"],
        replacement_line_count=1,
        snippet="",
        group_type="singleton",
    )


def _result(
    text: str,
    *,
    reasoning_details: "list[dict] | None" = None,
) -> CompletionResult:
    return CompletionResult(
        text=text,
        usage=TokenUsage(),
        reasoning_details=reasoning_details,
    )


def test_deepseek_happy_path_verdict_from_content_counter_at_zero() -> None:
    """Matrix case 1: the typical DeepSeek response — valid JSON in content
    AND populated reasoning_details. Vex parses verdict from content; the
    reasoning channel is consumed for telemetry only (counter at zero).
    """
    # Arrange
    client = MagicMock()
    client.complete.return_value = _result(
        text='{"verdict": "apply", "reason": "Safe replacement."}',
        reasoning_details=[
            {"type": "reasoning", "text": "Checked indent + control flow."}
        ],
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo(value):\n    return value\n",
        finding=_finding(),
    )

    # Assert
    assert verdict.verdict == "apply"
    assert "Safe replacement" in verdict.reason
    assert verifier.reasoning_missing_count == 0


def test_deepseek_empty_content_with_reasoning_fails_open_counter_increments() -> None:
    """Matrix case 2: model spent its think budget on the separate channel
    and emitted no content. Verdict fails open to ``apply``; counter
    increments so dogfood can spot the drift.
    """
    # Arrange
    client = MagicMock()
    client.complete.return_value = _result(
        text="",
        reasoning_details=[{"type": "reasoning", "text": "Long CoT trace..."}],
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo():\n    return None\n",
        finding=_finding(),
    )

    # Assert
    assert verdict.verdict == "apply"  # fail-open contract preserved
    assert verifier.reasoning_missing_count >= 1
    # Reasoning text must NOT leak into the verdict reason.
    assert "Long CoT" not in verdict.reason


@pytest.mark.parametrize(
    "malformed_reasoning",
    [
        "not a list — it's a bare string",  # provider returned a plain str
        {"unexpected": "shape"},              # provider returned a dict
        42,                                    # provider returned an int
        [],                                    # empty list — treat as no reasoning
    ],
)
def test_deepseek_malformed_reasoning_with_valid_content_parses_normally(
    malformed_reasoning,
) -> None:
    """Matrix case 3: ``reasoning_details`` is present but not the expected
    list-of-dicts shape. Verdict still parses from content; the missing
    counter does NOT fire because content is non-empty.
    """
    # Arrange
    client = MagicMock()
    client.complete.return_value = _result(
        text='{"verdict": "drop_cr_keep_prose", "reason": "Indent mismatch."}',
        reasoning_details=malformed_reasoning,
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo():\n        return\n",
        finding=_finding(),
    )

    # Assert
    assert verdict.verdict == "drop_cr_keep_prose"
    assert verifier.reasoning_missing_count == 0


def test_deepseek_silently_dropped_param_empty_content_matches_today() -> None:
    """Matrix case 4: OpenRouter routed to a backend that ignored the
    ``reasoning`` param — response has no ``reasoning_details`` at all,
    just empty content. This is today's pre-Vex-Option-C failure mode
    and must remain unchanged: fail-open to ``apply``, missing counter
    stays at zero (because reasoning was never observed).
    """
    # Arrange
    client = MagicMock()
    client.complete.return_value = _result(text="", reasoning_details=None)
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo():\n    return None\n",
        finding=_finding(),
    )

    # Assert
    assert verdict.verdict == "apply"  # existing fail-open contract
    assert verifier.reasoning_missing_count == 0


def test_deepseek_reasoning_text_with_verdict_shape_is_not_mined() -> None:
    """REVUE-324 TC15: even if the reasoning channel contains a string that
    looks like a verdict ('{"verdict": "reject_finding", ...}'), the
    parser must NEVER mine reasoning for the decision. Content is empty,
    so the verdict falls open to ``apply`` regardless.
    """
    # Arrange — reasoning_details contains a verdict-shaped string that
    # would, if mined, reject the finding. Vex must IGNORE this.
    client = MagicMock()
    client.complete.return_value = _result(
        text="",
        reasoning_details=[
            {
                "type": "reasoning",
                "text": '{"verdict": "reject_finding", "reason": "Stale issue."}',
            }
        ],
    )
    verifier = VexVerifier(ai_client=client)

    # Act
    verdict = verifier.verify(
        file_content="def foo():\n    return None\n",
        finding=_finding(),
    )

    # Assert
    assert verdict.verdict == "apply"  # fail-open, NOT the mined reject_finding
    assert "reject_finding" not in verdict.reason
    assert "Stale issue" not in verdict.reason

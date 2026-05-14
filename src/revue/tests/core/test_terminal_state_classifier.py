"""REVUE-246 AC3: terminal-state classifier for the tool-use loop.

The classifier converts the raw output of an agent turn (raw text, stop
reason, iteration counters) into exactly one of the three contract states:
``findings`` / ``clean`` / ``error``. Empty or unparseable text becomes
``error("invalid_response_schema")``, NOT inferred ``clean`` — that is the
rule that makes the 14K-line silent bail-out visible.

State handlers live in a registry, not an if/elif chain (see
``feedback_no_platform_elif`` in auto-memory).
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Findings shape — happy path
# ---------------------------------------------------------------------------


def test_findings_shape_routes_to_findings_state() -> None:
    """A well-formed findings response classifies to state ``findings`` and
    surfaces the findings array intact on the payload."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state
    raw = json.dumps({
        "status": "findings",
        "findings": [
            {
                "file_path": "src/a.py",
                "line_number": 12,
                "severity": "high",
                "issue": "SQL injection",
                "suggestion": "use parameterised queries",
                "confidence": 0.9,
                "category": "security",
            }
        ],
    })

    # Act
    state = classify_terminal_state(
        raw_text=raw,
        stop_reason="end_turn",
        iterations_used=2,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "findings"
    assert len(state.payload["findings"]) == 1
    assert state.payload["findings"][0]["file_path"] == "src/a.py"


# ---------------------------------------------------------------------------
# Clean shape
# ---------------------------------------------------------------------------


def test_clean_shape_routes_to_clean_state() -> None:
    """A well-formed clean response classifies to state ``clean`` with
    summary + confidence preserved."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state
    raw = json.dumps({
        "status": "clean",
        "summary": "reviewed 14 files; nothing of architectural concern",
        "confidence": 0.85,
    })

    # Act
    state = classify_terminal_state(
        raw_text=raw,
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "clean"
    assert state.payload["summary"].startswith("reviewed")
    assert state.payload["confidence"] == 0.85


# ---------------------------------------------------------------------------
# Error shapes — five fixtures, one per error code (AC9 contract)
# ---------------------------------------------------------------------------


def test_empty_text_classifies_as_invalid_response_schema() -> None:
    """An empty final response — the 14K-line bail-out — must classify as
    ``error("invalid_response_schema")``. This is the exact failure mode
    REVUE-246 exists to make visible."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state

    # Act
    state = classify_terminal_state(
        raw_text="",
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "invalid_response_schema"


def test_legacy_findings_array_only_classifies_as_invalid_response_schema() -> None:
    """The legacy ``{"findings": []}`` shape — what reviewers used to emit —
    is now rejected. AC8 forbids a shim; old responses must surface as a
    schema error so the prompt gets fixed, not silently accepted."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state

    # Act
    state = classify_terminal_state(
        raw_text=json.dumps({"findings": []}),
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "invalid_response_schema"


def test_hit_cap_with_empty_text_classifies_as_max_iterations_no_verdict() -> None:
    """When the loop ran out of iterations AND the model never produced a
    parseable verdict, the right code is ``max_iterations_no_verdict`` —
    distinguishes the budget-exhaustion path from a generic schema mismatch
    so operators can raise the per-agent budget rather than chase prompts."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state

    # Act
    state = classify_terminal_state(
        raw_text="",
        stop_reason="end_turn",
        iterations_used=5,
        max_iterations=5,
        hit_iteration_cap=True,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "max_iterations_no_verdict"
    assert state.payload["error"]["iterations_used"] == 5


def test_refusal_stop_reason_classifies_as_model_refusal() -> None:
    """A refusal stop reason — the model declined to respond — surfaces as
    its own code so operators don't conflate it with a prompt-shape bug."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state

    # Act
    state = classify_terminal_state(
        raw_text="I can't help with that.",
        stop_reason="refusal",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "model_refusal"


def test_agent_self_declared_tool_unavailable_passes_through() -> None:
    """``tool_unavailable`` is *self-declared by the agent* after fallback
    attempts fail — see the spec under "Tool errors mid-review are
    non-terminal". The classifier must preserve the code, not relabel it."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state
    raw = json.dumps({
        "status": "error",
        "error": {
            "code": "tool_unavailable",
            "message": "read_file failed for every file in the diff",
        },
    })

    # Act
    state = classify_terminal_state(
        raw_text=raw,
        stop_reason="end_turn",
        iterations_used=3,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "tool_unavailable"


def test_unknown_error_code_is_normalised_to_internal_error() -> None:
    """If the model invents an error code outside the closed set, the
    classifier surfaces it as ``internal_error`` rather than passing the
    arbitrary value through — preserves the closed-set guarantee for
    downstream consumers."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state
    raw = json.dumps({
        "status": "error",
        "error": {"code": "made_up_code", "message": "huh"},
    })

    # Act
    state = classify_terminal_state(
        raw_text=raw,
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "internal_error"


# ---------------------------------------------------------------------------
# Schema-violation tests — exclusivity is enforced
# ---------------------------------------------------------------------------


def test_findings_branch_without_findings_array_is_invalid() -> None:
    """``status: findings`` with no ``findings`` field is a half-completed
    response — must surface as a schema error so we don't infer an empty list."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state

    # Act
    state = classify_terminal_state(
        raw_text=json.dumps({"status": "findings"}),
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "invalid_response_schema"


def test_clean_without_summary_is_invalid() -> None:
    """A clean verdict without summary+confidence — see AC1 — is exactly
    the silent-clean bail-out we are trying to eliminate. Reject."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state

    # Act
    state = classify_terminal_state(
        raw_text=json.dumps({"status": "clean"}),
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "invalid_response_schema"


def test_conflicting_shape_findings_plus_error_is_invalid() -> None:
    """A response carrying both ``findings`` and ``error`` violates the
    discriminator — the classifier must reject it, not silently pick one."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state
    raw = json.dumps({
        "status": "findings",
        "findings": [],
        "error": {"code": "internal_error", "message": "x"},
    })

    # Act
    state = classify_terminal_state(
        raw_text=raw,
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "error"
    assert state.payload["error"]["code"] == "invalid_response_schema"


# ---------------------------------------------------------------------------
# Markdown-fence tolerance — same as legacy parser
# ---------------------------------------------------------------------------


def test_markdown_fences_are_stripped_before_parsing() -> None:
    """Models sometimes wrap JSON in ``` fences. The classifier strips them
    so a perfectly valid three-state payload doesn't get misclassified as a
    schema error just because the model added prose padding."""
    # Arrange
    from revue.core.terminal_state import classify_terminal_state
    fenced = (
        "```json\n"
        + json.dumps({"status": "clean", "summary": "ok", "confidence": 0.9})
        + "\n```"
    )

    # Act
    state = classify_terminal_state(
        raw_text=fenced,
        stop_reason="end_turn",
        iterations_used=1,
        max_iterations=5,
        hit_iteration_cap=False,
    )

    # Assert
    assert state.state == "clean"

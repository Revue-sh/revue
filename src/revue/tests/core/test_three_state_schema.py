"""REVUE-246 AC1+AC2: three-state reviewer response schema.

Every reviewer agent must emit exactly one of three top-level shapes,
discriminated by a ``status`` field:

  * ``status: findings`` — at least one issue flagged
  * ``status: clean``    — reviewed successfully, nothing to flag
  * ``status: error``    — could not produce a verdict

Exclusivity is enforced by the discriminator; the Anthropic structured-outputs
grammar compiles the union so non-conforming responses raise at API boundary.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# AC1 — schema definition and validation
# ---------------------------------------------------------------------------


def test_three_state_schema_top_level_is_a_tagged_union() -> None:
    """The schema must be a tagged union of three shapes — not a flat object
    with optional fields. Without the union, the grammar can't enforce
    exclusivity: a response could carry both ``findings`` and ``error``
    blocks and the parser would have to pick one arbitrarily."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA

    # Act
    branches = THREE_STATE_SCHEMA.get("anyOf") or THREE_STATE_SCHEMA.get("oneOf")

    # Assert
    assert "anyOf" in THREE_STATE_SCHEMA or "oneOf" in THREE_STATE_SCHEMA, (
        "three-state schema must be a tagged union — anyOf or oneOf at top level"
    )
    assert len(branches) == 3, "exactly three branches: findings, clean, error"


def test_three_state_schema_branches_pin_status_to_const() -> None:
    """Each branch must pin ``status`` to a single ``const`` value — that is
    the discriminator. Without ``const`` the grammar can't tell branches apart
    and the union collapses to ``any``."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA
    branches = THREE_STATE_SCHEMA.get("anyOf") or THREE_STATE_SCHEMA["oneOf"]

    # Act
    status_consts = sorted(
        branch["properties"]["status"]["const"] for branch in branches
    )

    # Assert
    assert status_consts == ["clean", "error", "findings"]


def test_findings_branch_carries_findings_array() -> None:
    """The findings branch reuses the per-finding item schema from the
    existing FINDINGS_SCHEMA — exact same item shape so downstream parsers
    don't fork on the union branch."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA, FINDING_ITEM
    branches = THREE_STATE_SCHEMA.get("anyOf") or THREE_STATE_SCHEMA["oneOf"]

    # Act
    findings_branch = next(
        b for b in branches if b["properties"]["status"]["const"] == "findings"
    )

    # Assert — same per-finding shape as the legacy schema
    assert findings_branch["properties"]["findings"]["type"] == "array"
    assert findings_branch["properties"]["findings"]["items"] == FINDING_ITEM
    # Assert — findings list is required on this branch (an empty branch
    # carrying only `status: findings` is the silent bail-out being eliminated)
    assert "findings" in findings_branch["required"]


def test_clean_branch_requires_summary_and_confidence() -> None:
    """Per Quinn's review: a clean verdict without a summary or confidence
    is indistinguishable from a silent bail-out. The fields force the
    reviewer to explicitly report what they reviewed and how sure they are."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA
    branches = THREE_STATE_SCHEMA.get("anyOf") or THREE_STATE_SCHEMA["oneOf"]

    # Act
    clean_branch = next(
        b for b in branches if b["properties"]["status"]["const"] == "clean"
    )

    # Assert
    required = set(clean_branch["required"])
    assert "summary" in required
    assert "confidence" in required
    assert clean_branch["properties"]["summary"]["type"] == "string"
    assert clean_branch["properties"]["confidence"]["type"] == "number"


def test_error_branch_enumerates_codes() -> None:
    """Error codes are a closed set so consumers can route on them without
    string-matching on free-form messages."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA, ERROR_CODES
    expected_codes = {
        "max_iterations_no_verdict",
        "invalid_response_schema",
        "tool_unavailable",
        "model_refusal",
        "internal_error",
    }
    branches = THREE_STATE_SCHEMA.get("anyOf") or THREE_STATE_SCHEMA["oneOf"]

    # Act
    error_branch = next(
        b for b in branches if b["properties"]["status"]["const"] == "error"
    )

    # Assert — codes are closed-set in both ERROR_CODES and the schema enum
    assert set(ERROR_CODES) == expected_codes
    error_obj = error_branch["properties"]["error"]
    assert error_obj["type"] == "object"
    assert set(error_obj["properties"]["code"]["enum"]) == expected_codes


def test_branches_disable_additional_properties() -> None:
    """Every object in the union must declare additionalProperties: false —
    otherwise a malformed response carrying *both* findings and clean fields
    parses as the first branch the validator tries, defeating exclusivity."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA

    # Act
    branches = THREE_STATE_SCHEMA.get("anyOf") or THREE_STATE_SCHEMA["oneOf"]

    # Assert
    for b in branches:
        assert b.get("additionalProperties") is False, (
            f"branch {b['properties']['status']['const']!r} must set "
            f"additionalProperties: false"
        )


def test_schema_excludes_anthropic_grammar_constraints_that_compiler_rejects() -> None:
    """Anthropic's grammar compiler rejects minimum/maximum/minLength/maxLength
    anywhere in the schema. Tests on the legacy FINDINGS_SCHEMA pin this for
    the per-finding shape; this test extends the same guarantee to the union."""
    # Arrange
    from revue.core.finding_schema import THREE_STATE_SCHEMA
    forbidden = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            present = forbidden & set(node.keys())
            assert not present, (
                f"unsupported grammar constraint(s) {sorted(present)} in node: {node}"
            )
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    # Act + Assert — recursive walk asserts at every node
    _walk(THREE_STATE_SCHEMA)


# ---------------------------------------------------------------------------
# AC2 — output_config enforces the schema
# ---------------------------------------------------------------------------


def test_output_config_for_three_state_returns_schema_wrapped() -> None:
    """The Anthropic SDK takes ``output_config={"format": {"type": "json_schema",
    "schema": ...}}``. Helper must return that exact envelope so callers can
    pass it through unchanged — same convention as the legacy
    ``output_config_for_findings``."""
    # Arrange
    from revue.core.finding_schema import (
        THREE_STATE_SCHEMA, output_config_for_three_state,
    )

    # Act
    cfg = output_config_for_three_state()

    # Assert
    assert cfg["format"]["type"] == "json_schema"
    assert cfg["format"]["schema"] == THREE_STATE_SCHEMA


def test_output_config_for_three_state_returns_fresh_dict() -> None:
    """Each call returns a fresh dict so callers can mutate without
    poisoning the module-level constant (same defensiveness as the legacy
    helper)."""
    # Arrange
    from revue.core.finding_schema import output_config_for_three_state
    a = output_config_for_three_state()
    b = output_config_for_three_state()

    # Act — mutate the first instance
    a["format"]["schema"] = "mutated"

    # Assert — the second instance is unaffected
    assert b["format"]["schema"] != "mutated"

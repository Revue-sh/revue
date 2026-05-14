"""REVUE-241: shared findings JSON schema for Anthropic structured outputs.

The schema is the contract that constrains the model's final text response
after a tool-use loop. Three things must hold for the cache to behave and
for the schema to satisfy Anthropic's grammar-compilation limits:

  * ``code_replacement`` and ``replacement_line_count`` MUST be optional.
    Forcing them required would coerce the model into fabricating a fix
    just to satisfy the schema — the exact false-precision failure mode
    REVUE-241 exists to prevent.
  * No numeric constraints (``minimum``/``maximum``/``multipleOf``) and no
    string ``minLength``/``maxLength``. Anthropic's grammar compiler does
    not accept them and will 400 the request.
  * All objects must declare ``additionalProperties: false`` — required
    by the docs.

Tests pin every contract so a future edit can't quietly regress one.
"""
from __future__ import annotations


def test_findings_schema_top_level_is_object_wrapping_findings_array() -> None:
    """The top-level shape is ``{type: object, properties: {findings: array}}``
    — not a bare top-level array. The docs only demonstrate object wrappers
    and the SDK's grammar compiler is more forgiving with them."""
    from revue.core.finding_schema import FINDINGS_SCHEMA

    assert FINDINGS_SCHEMA["type"] == "object"
    assert FINDINGS_SCHEMA.get("additionalProperties") is False
    assert "findings" in FINDINGS_SCHEMA["properties"]
    assert FINDINGS_SCHEMA["properties"]["findings"]["type"] == "array"
    assert FINDINGS_SCHEMA["required"] == ["findings"]


def test_findings_schema_code_replacement_is_optional() -> None:
    """``code_replacement`` must NOT be in the item's required list — the
    model needs the freedom to omit it when the fix is descriptive only.
    Forcing it required is how REVUE-241 would *create* the regression it
    was added to prevent."""
    from revue.core.finding_schema import FINDINGS_SCHEMA

    item = FINDINGS_SCHEMA["properties"]["findings"]["items"]
    required = item.get("required", [])
    assert "code_replacement" not in required, (
        "code_replacement must be optional; making it required forces the "
        "model to fabricate a fix to satisfy the schema"
    )
    assert "replacement_line_count" not in required, (
        "replacement_line_count must be optional for the same reason as "
        "code_replacement — it only has meaning paired with a fix"
    )


def test_findings_schema_required_fields_are_the_essential_ones() -> None:
    """The minimum required fields are the ones every finding must have:
    file_path, line_number, severity, issue, suggestion, confidence,
    category. Anything else stays optional."""
    from revue.core.finding_schema import FINDINGS_SCHEMA

    item = FINDINGS_SCHEMA["properties"]["findings"]["items"]
    required = set(item["required"])
    assert required == {
        "file_path", "line_number", "severity", "issue",
        "suggestion", "confidence", "category",
    }


def test_findings_schema_uses_enums_for_severity_and_category() -> None:
    """Enums are the right tool for closed-set fields — the grammar will
    constrain output to exactly these values, eliminating the
    'major'/'critical' synonyms that the existing _SEV_MAP papered over."""
    from revue.core.finding_schema import FINDINGS_SCHEMA

    props = FINDINGS_SCHEMA["properties"]["findings"]["items"]["properties"]
    assert sorted(props["severity"]["enum"]) == sorted([
        "high", "medium", "low", "info",
    ])
    assert sorted(props["category"]["enum"]) == sorted([
        "architecture", "security", "performance", "code-quality",
    ])


def test_findings_schema_has_no_unsupported_constraints() -> None:
    """Walk the schema and assert none of the constraint keywords that
    Anthropic's grammar compiler rejects are present anywhere.

    Per the structured-outputs docs:
      * ``minimum`` / ``maximum`` / ``multipleOf`` — NOT supported
      * ``minLength`` / ``maxLength`` — NOT supported
      * Array ``minItems`` — only 0 or 1 supported (we use neither)
    """
    from revue.core.finding_schema import FINDINGS_SCHEMA

    forbidden = {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            present = forbidden & set(node.keys())
            assert not present, (
                f"unsupported grammar constraint(s) {sorted(present)} found "
                f"in schema node: {node}"
            )
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(FINDINGS_SCHEMA)


def test_openai_response_format_for_findings_shape() -> None:
    """The OpenAI-style ``response_format`` wraps the schema in
    ``{type: json_schema, json_schema: {name, strict, schema}}`` — different
    from Anthropic's ``{format: {type: json_schema, schema}}``. The two
    helpers keep the conversion at the call boundary so callers don't need
    to know which provider style is in play."""
    from revue.core.finding_schema import (
        FINDINGS_SCHEMA, openai_response_format_for_findings,
    )

    rf = openai_response_format_for_findings()
    assert rf["type"] == "json_schema"
    assert "json_schema" in rf
    assert rf["json_schema"]["name"], "json_schema.name is required by OpenAI"
    # strict=True enables OpenAI's grammar-constrained sampling on supported
    # backends; on OpenRouter, support is best-effort depending on the
    # routed model. Tests on non-OpenAI backends may need to flip this.
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == FINDINGS_SCHEMA


def test_openai_response_format_returns_fresh_dict() -> None:
    """Each call returns a new dict so callers can mutate without affecting
    siblings — same defensiveness as ``output_config_for_findings``."""
    from revue.core.finding_schema import openai_response_format_for_findings

    a = openai_response_format_for_findings()
    b = openai_response_format_for_findings()
    a["json_schema"]["strict"] = False
    assert b["json_schema"]["strict"] is True


def test_findings_schema_objects_disable_additional_properties() -> None:
    """Every object in the schema must set ``additionalProperties: false``
    — required by the structured-outputs docs."""
    from revue.core.finding_schema import FINDINGS_SCHEMA

    def _walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, (
                    f"object at {path} missing additionalProperties: false"
                )
            for key, value in node.items():
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                _walk(value, f"{path}[{idx}]")

    _walk(FINDINGS_SCHEMA, "$")

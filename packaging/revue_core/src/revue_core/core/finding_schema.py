"""Shared JSON schema for reviewer-agent findings (REVUE-241).

Used as the ``output_config.format.schema`` argument to Anthropic's
``messages.create`` so the final text response from a reviewer agent is
grammar-constrained to valid JSON matching this shape — even after a
multi-turn tool-use loop where prose-drift would otherwise let the model
emit "Based on my analysis..." instead of a parseable findings array.

Design decisions pinned by tests in ``test_finding_schema.py``:

* Top-level shape is ``{findings: [...]}`` (object wrapper), not a bare
  array — matches every example in the structured-outputs docs.
* ``code_replacement`` and ``replacement_line_count`` are OPTIONAL.
  Marking them required would push the model to fabricate fixes when none
  exist, which is exactly the false-precision failure mode REVUE-241 was
  added to eliminate.
* ``severity`` and ``category`` are enums — the grammar constrains output
  to the four values each, dropping the "critical"/"major" synonym
  noise that the existing ``_SEV_MAP`` had to paper over.
* No ``minimum``/``maximum``/``minLength``/``maxLength`` anywhere — the
  Anthropic grammar compiler rejects them. SDK-side validation would
  belong in the parser, not the schema.

Kept as a module-level constant so one shared schema is sent across every
reviewer call. Anthropic caches compiled grammars for 24h keyed on the
schema; sharing it means one compilation per day rather than four.
"""
from __future__ import annotations

from typing import Any, Final

_SEVERITY_VALUES: Final[list[str]] = ["high", "medium", "low", "info"]
_CATEGORY_VALUES: Final[list[str]] = [
    "architecture", "security", "performance", "code-quality",
]

FINDING_ITEM: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "file_path": {"type": "string"},
        "line_number": {"type": "integer"},
        "severity": {"type": "string", "enum": _SEVERITY_VALUES},
        "issue": {"type": "string"},
        "suggestion": {"type": "string"},
        "confidence": {"type": "number"},
        "category": {"type": "string", "enum": _CATEGORY_VALUES},
        "code_replacement": {
            "type": "array",
            "items": {"type": "string"},
        },
        "replacement_line_count": {"type": "integer"},
    },
    "required": [
        "file_path", "line_number", "severity", "issue",
        "suggestion", "confidence", "category",
    ],
}

FINDINGS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": FINDING_ITEM,
        },
    },
    "required": ["findings"],
}


def output_config_for_findings() -> dict[str, Any]:
    """Convenience: the full ``output_config`` dict ready to pass to the SDK.

    Returns a fresh dict each call so callers can mutate it without affecting
    other reviewers (and so cache-control or other future siblings can be
    layered on without poisoning the module-level constant).
    """
    return {
        "format": {
            "type": "json_schema",
            "schema": FINDINGS_SCHEMA,
        }
    }


# ---------------------------------------------------------------------------
# REVUE-246: three-state reviewer response contract.
#
# Every reviewer agent returns exactly one of three top-level shapes,
# discriminated by ``status``:
#   * findings — at least one issue flagged
#   * clean    — reviewed successfully, nothing to flag
#   * error    — could not produce a verdict (model refusal, schema mismatch,
#                tool exhaustion, max iterations, etc.)
#
# Exclusivity is enforced by the grammar via ``anyOf`` + a per-branch
# ``const`` on ``status``. The legacy ``FINDINGS_SCHEMA`` stays available for
# callers that still want the array-only response (e.g. Nova synthesis is
# unchanged — only the four reviewer agents migrate to the three-state
# contract).
# ---------------------------------------------------------------------------

ERROR_CODES: Final[list[str]] = [
    "max_iterations_no_verdict",
    "invalid_response_schema",
    "tool_unavailable",
    "model_refusal",
    "internal_error",
]


_FINDINGS_BRANCH: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "const": "findings"},
        "findings": {
            "type": "array",
            "items": FINDING_ITEM,
        },
        # summary is OPTIONAL for the findings branch — the per-finding
        # ``issue`` / ``suggestion`` strings already convey the detail.
        "summary": {"type": "string"},
    },
    "required": ["status", "findings"],
}


_CLEAN_BRANCH: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "const": "clean"},
        # REQUIRED per Quinn: a bare ``status: clean`` is indistinguishable
        # from a silent bail-out. Forcing summary + confidence makes the
        # reviewer explicitly report what they reviewed and how sure they are.
        "summary": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["status", "summary", "confidence"],
}


_ERROR_DETAIL: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "code": {"type": "string", "enum": ERROR_CODES},
        "message": {"type": "string"},
        "iterations_used": {"type": "integer"},
    },
    "required": ["code", "message"],
}


_ERROR_BRANCH: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "const": "error"},
        "error": _ERROR_DETAIL,
    },
    "required": ["status", "error"],
}


THREE_STATE_SCHEMA: Final[dict[str, Any]] = {
    "anyOf": [_FINDINGS_BRANCH, _CLEAN_BRANCH, _ERROR_BRANCH],
}


def output_config_for_three_state() -> dict[str, Any]:
    """Anthropic ``output_config`` wrapping :data:`THREE_STATE_SCHEMA`.

    Returns a fresh dict so callers can layer cache-control or other fields
    on without poisoning the module-level constant.
    """
    import copy
    return {
        "format": {
            "type": "json_schema",
            "schema": copy.deepcopy(THREE_STATE_SCHEMA),
        }
    }


def openai_response_format_for_three_state() -> dict[str, Any]:
    """OpenAI-style ``response_format`` wrapping :data:`THREE_STATE_SCHEMA`.

    Mirrors :func:`openai_response_format_for_findings` for the three-state
    contract — used by OpenAI-compatible reviewer clients (REVUE-241 wired
    structured outputs across all four providers, and the new contract has
    to match)."""
    import copy
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "three_state_response",
            "strict": True,
            "schema": copy.deepcopy(THREE_STATE_SCHEMA),
        },
    }


def openai_response_format_for_findings() -> dict[str, Any]:
    """OpenAI-style ``response_format`` for chat.completions.create.

    The shape differs from Anthropic's: OpenAI nests the schema under a
    ``json_schema`` key alongside ``name`` and ``strict``. ``strict: True``
    enables OpenAI's grammar-constrained sampling on supported backends —
    on OpenRouter, support is best-effort and depends on the model the
    request is routed to.

    Returns a fresh dict each call (same defensiveness as
    ``output_config_for_findings``).
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "findings_response",
            "strict": True,
            "schema": FINDINGS_SCHEMA,
        },
    }

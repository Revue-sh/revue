"""Terminal-state classifier for reviewer-agent responses (REVUE-246).

Every reviewer turn ends with a raw text payload, a stop reason, and an
iteration counter. The classifier converts those into exactly one of the
three contract states:

  * ``findings`` — at least one issue flagged
  * ``clean``    — reviewed successfully, nothing to flag
  * ``error``    — could not produce a verdict

Empty or unparseable text becomes ``error("invalid_response_schema")``, NOT
inferred ``clean`` — that is the rule that makes the 14K-line silent
bail-out visible in REVUE-244's AC10 dogfood.

State handlers live in a registry (see ``_BRANCH_HANDLERS``), not in an
if/elif chain — closed-set discriminator dispatch, OCP-friendly when a new
branch is added.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Final

from .finding_schema import ERROR_CODES


# ---------------------------------------------------------------------------
# TerminalState — typed result returned by ``classify_terminal_state``
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TerminalState:
    """Outcome of one reviewer turn.

    ``state`` is the closed-set discriminator ("findings" / "clean" / "error").
    ``payload`` is the validated three-state dict — callers downstream can
    rely on its shape having been checked by :func:`classify_terminal_state`.
    """
    state: str
    payload: dict[str, Any]

    @classmethod
    def findings(cls, payload: dict[str, Any]) -> "TerminalState":
        return cls(state="findings", payload=payload)

    @classmethod
    def clean(cls, payload: dict[str, Any]) -> "TerminalState":
        return cls(state="clean", payload=payload)

    @classmethod
    def error(
        cls,
        code: str,
        message: str,
        *,
        iterations_used: int | None = None,
    ) -> "TerminalState":
        # Closed-set enforcement: an unrecognised code collapses to
        # internal_error so consumers can rely on the enum.
        if code not in ERROR_CODES:
            message = f"{message} (original code: {code!r})"
            code = "internal_error"
        error_detail: dict[str, Any] = {"code": code, "message": message}
        if iterations_used is not None:
            error_detail["iterations_used"] = iterations_used
        return cls(
            state="error",
            payload={"status": "error", "error": error_detail},
        )


# ---------------------------------------------------------------------------
# Internal: parsing helpers
# ---------------------------------------------------------------------------


def _strip_markdown_fence(text: str) -> str:
    """Strip a single leading/trailing triple-backtick fence — same tolerance
    as the legacy ``analyse`` parser. Without this, a perfectly valid
    three-state payload wrapped in ``` would be misclassified as a schema
    error simply because the model added a prose envelope."""
    clean = text.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.split("\n")[:-1])
    return clean.strip()


def _try_parse_json(text: str) -> "dict[str, Any] | None":
    """Return the parsed dict or None on any parse failure / non-dict root.

    Lists, scalars and arrays aren't valid three-state payloads — the schema
    is always an object — so non-dict roots collapse to None.
    """
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Branch handlers — registry, not if/elif
# ---------------------------------------------------------------------------


def _handle_findings(parsed: dict[str, Any]) -> TerminalState:
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return TerminalState.error(
            code="invalid_response_schema",
            message=(
                "response carries status=findings but no findings array — "
                "the schema requires both"
            ),
        )
    # Reject conflicting union shapes outright — exclusivity is the whole
    # point of the discriminator.
    if "error" in parsed:
        return TerminalState.error(
            code="invalid_response_schema",
            message=(
                "response carries both status=findings and an error block; "
                "the three-state contract is exclusive"
            ),
        )
    return TerminalState.findings(parsed)


def _handle_clean(parsed: dict[str, Any]) -> TerminalState:
    summary = parsed.get("summary")
    confidence = parsed.get("confidence")
    if not isinstance(summary, str) or not summary.strip():
        return TerminalState.error(
            code="invalid_response_schema",
            message=(
                "status=clean response missing required 'summary' field; "
                "a bare clean is indistinguishable from a silent bail-out"
            ),
        )
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return TerminalState.error(
            code="invalid_response_schema",
            message="status=clean response missing required numeric 'confidence' field",
        )
    if "findings" in parsed or "error" in parsed:
        return TerminalState.error(
            code="invalid_response_schema",
            message="status=clean response carries other branch fields",
        )
    return TerminalState.clean(parsed)


def _handle_error(parsed: dict[str, Any]) -> TerminalState:
    err = parsed.get("error")
    if not isinstance(err, dict):
        return TerminalState.error(
            code="invalid_response_schema",
            message="status=error response missing required 'error' object",
        )
    code = err.get("code")
    message = err.get("message") or ""
    if not isinstance(code, str):
        return TerminalState.error(
            code="invalid_response_schema",
            message="error.code must be a string from the closed set",
        )
    iterations_used = err.get("iterations_used")
    iter_kw = iterations_used if isinstance(iterations_used, int) else None
    # Let TerminalState.error normalise unknown codes to internal_error so the
    # closed-set guarantee holds for downstream consumers.
    return TerminalState.error(
        code=code,
        message=str(message),
        iterations_used=iter_kw,
    )


_BRANCH_HANDLERS: Final[dict[str, Callable[[dict[str, Any]], TerminalState]]] = {
    "findings": _handle_findings,
    "clean": _handle_clean,
    "error": _handle_error,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_terminal_state(
    *,
    raw_text: str,
    stop_reason: str | None,
    iterations_used: int,
    max_iterations: int,
    hit_iteration_cap: bool,
) -> TerminalState:
    """Classify the terminal output of a reviewer turn into one contract state.

    Parameters
    ----------
    raw_text:
        The final text content from the assistant turn. May be empty.
    stop_reason:
        Anthropic stop reason (``end_turn`` / ``refusal`` / ``tool_use`` / ...).
    iterations_used / max_iterations:
        Tool-loop counters — surfaced so the ``max_iterations_no_verdict``
        error carries the iteration count.
    hit_iteration_cap:
        True when the loop ran out of iterations before the model produced
        a finalized verdict. Distinguishes "budget exhausted, no verdict"
        from a generic schema mismatch — operators triage them differently.
    """
    # Refusal takes precedence — even if the model emitted text, the meaning
    # of that text is "I won't", not a review verdict.
    if stop_reason == "refusal":
        return TerminalState.error(
            code="model_refusal",
            message="model returned stop_reason=refusal",
            iterations_used=iterations_used,
        )

    parsed = _try_parse_json(_strip_markdown_fence(raw_text))

    if parsed is None or "status" not in parsed:
        # Empty / unparseable / no discriminator → the silent bail-out we
        # exist to surface. Distinguish budget exhaustion (operator should
        # raise the per-agent iteration cap) from generic schema mismatch
        # (the prompt needs work).
        code = (
            "max_iterations_no_verdict" if hit_iteration_cap
            else "invalid_response_schema"
        )
        message = (
            "tool loop ran out of iterations before the model emitted a "
            "structured verdict" if hit_iteration_cap else
            "response was empty, not JSON, or missing the 'status' discriminator"
        )
        return TerminalState.error(
            code=code,
            message=message,
            iterations_used=iterations_used,
        )

    handler = _BRANCH_HANDLERS.get(parsed["status"])
    if handler is None:
        return TerminalState.error(
            code="invalid_response_schema",
            message=(
                f"response.status={parsed['status']!r} is not one of "
                f"{sorted(_BRANCH_HANDLERS)}"
            ),
            iterations_used=iterations_used,
        )
    return handler(parsed)

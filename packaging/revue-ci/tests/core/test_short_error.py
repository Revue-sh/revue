"""REVUE-241: `_short_error` must surface enough detail for operators to
triage an agent failure without scrolling back through the full log.

Current pain (from the 2026-05-13 GitHub CI run on PR #26): the message
``⚠ Agent maya failed: prompt is too long (200887 to…`` is truncated and
omits the exception class — operators can't tell if it's an SDK
``BadRequestError`` (transport-layer) or a ``ValueError`` (config-layer)
without going back to the raw stack trace.
"""
from __future__ import annotations

from revue_core.core.pipeline import _classify_error_body, _short_error


# ---------------------------------------------------------------------------
# _classify_error_body: pure single-responsibility helper. No decoration,
# no knowledge of error_type/call_site — just raw string → condensed body.
# Split out from _short_error per SRP (REVUE-241).
# ---------------------------------------------------------------------------


def test_classify_empty_string_returns_unknown_error() -> None:
    assert _classify_error_body("") == "unknown error"


def test_classify_rate_limit_preserves_http_code() -> None:
    """``rate limit exceeded (429)`` not bare ``rate limit exceeded`` — the
    code tells operators whether to back off (429), increase quota (529),
    or check for an Anthropic incident (529 vs vendor outage)."""
    out = _classify_error_body("HTTP 429: rate_limit_error")
    assert "rate limit exceeded" in out
    assert "(429)" in out


def test_classify_timeout_returns_timed_out() -> None:
    """No HTTP code on a client-side timeout — there was no response."""
    assert _classify_error_body("request timeout after 90s") == "timed out"


def test_classify_authentication_error_preserves_code() -> None:
    """401 (bad credentials) vs 403 (forbidden / wrong scope) need different
    fixes — preserving the code routes the operator to the right runbook."""
    assert _classify_error_body("401 unauthorized") == "authentication error (401)"
    assert _classify_error_body("403 forbidden") == "authentication error (403)"


def test_classify_server_error_preserves_code() -> None:
    """500 (server bug, retry won't help) vs 502/503 (transient, retry will)
    — preserve the code so retry logic in the log makes sense to readers."""
    assert _classify_error_body("HTTP 503 service unavailable") == "server error (503)"
    assert _classify_error_body("502 bad gateway") == "server error (502)"
    assert _classify_error_body("500 internal server error") == "server error (500)"


def test_classify_unknown_error_returns_first_line_truncated() -> None:
    """Fallback path: long single-line error capped at 120 chars + ellipsis;
    multi-line errors return only the first line."""
    long = "x" * 200
    out = _classify_error_body(long)
    assert out.endswith("…")
    assert len(out) == 121

    multiline = "first line\nsecond line\nthird line"
    assert _classify_error_body(multiline) == "first line"


def test_short_error_includes_type_prefix_when_provided() -> None:
    """``[BadRequestError] prompt is too long: 200887 tokens > 200000``
    beats ``prompt is too long: 200887 to…`` — the class name routes triage
    (config vs transport vs auth) in a single glance."""
    out = _short_error(
        "prompt is too long: 200887 tokens > 200000",
        error_type="BadRequestError",
    )
    assert out.startswith("[BadRequestError] ")
    assert "prompt is too long" in out


def test_short_error_appends_call_site_when_provided() -> None:
    """Call-site appears as a trailing tag so the line stays readable
    when grepped — ``[BadRequestError] msg (at AnthropicClient.complete_with_tools)``."""
    out = _short_error(
        "prompt is too long: 200887 tokens > 200000",
        error_type="BadRequestError",
        call_site="AnthropicClient.complete_with_tools",
    )
    assert "(at AnthropicClient.complete_with_tools)" in out


def test_short_error_without_type_falls_back_to_message_only() -> None:
    """Backwards compatibility: callers that don't supply the new metadata
    get the existing condensed-line behaviour, no breakage."""
    out = _short_error("rate_limit_error: too many requests")
    assert "rate limit exceeded" in out.lower()


def test_short_error_known_classifiers_still_match_with_type() -> None:
    """The rate-limit / timeout / auth classifiers still kick in even when
    a type prefix is supplied — the type augments, doesn't replace."""
    out = _short_error("Error code: 429 - rate_limit_error", error_type="APIStatusError")
    assert "rate limit exceeded" in out.lower()
    assert "[APIStatusError]" in out


def test_short_error_empty_input_still_returns_unknown_error() -> None:
    out = _short_error("")
    assert out == "unknown error"

"""Regression: _dispatch_tool catches every handler exception, not just TypeError.

Previously the dispatcher narrowed its catch to TypeError (the kwargs-mismatch
case). If a handler raised KeyError / AttributeError / ValueError from its
own body, the exception escaped the tool loop and aborted the whole review.
The model never saw the failure and couldn't self-correct.

These tests pin the broadened catch: any exception is converted to a
tool_result with is_error=True so the model can retry.
"""
from __future__ import annotations

import pytest

from revue.core.tool_loop import _dispatch_tool


def test_typeerror_from_signature_mismatch_returns_error_result():
    """TypeError (schema mismatch) still produces an error tool_result."""
    def handler(*, required: str):  # noqa: ARG001
        return None

    result = _dispatch_tool({"x": handler}, "x", tool_input={"wrong_kwarg": 1})

    assert result.is_error is True
    assert "x" in result.content


def test_keyerror_from_handler_body_returns_error_result():
    """KeyError raised inside the handler must be caught, not propagated."""
    def handler(**_):
        d: dict = {}
        return d["missing-key"]  # raises KeyError

    result = _dispatch_tool({"x": handler}, "x", tool_input={})

    assert result.is_error is True
    assert "KeyError" in result.content


def test_attributeerror_from_handler_body_returns_error_result():
    """AttributeError on a None attribute access must be caught."""
    def handler(**_):
        return None.does_not_exist  # noqa: B018

    result = _dispatch_tool({"x": handler}, "x", tool_input={})

    assert result.is_error is True
    assert "AttributeError" in result.content


def test_valueerror_from_handler_body_returns_error_result():
    """ValueError from a handler must be caught, not propagated."""
    def handler(**_):
        raise ValueError("bad input")

    result = _dispatch_tool({"x": handler}, "x", tool_input={})

    assert result.is_error is True
    assert "ValueError" in result.content
    assert "bad input" in result.content


def test_runtimeerror_from_handler_body_returns_error_result():
    """Any unexpected exception type is caught — the dispatcher is the last line."""
    def handler(**_):
        raise RuntimeError("boom")

    result = _dispatch_tool({"x": handler}, "x", tool_input={})

    assert result.is_error is True
    assert "RuntimeError" in result.content


def test_keyboardinterrupt_still_propagates():
    """Non-Exception system signals (KeyboardInterrupt) must NOT be swallowed.

    `except Exception` is intentional — it excludes BaseException-derived
    interrupts so Ctrl-C still aborts the review process.
    """
    def handler(**_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _dispatch_tool({"x": handler}, "x", tool_input={})

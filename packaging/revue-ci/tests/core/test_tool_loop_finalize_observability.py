"""REVUE-241 Gap 2 follow-up: forced-finalize must emit its own outcome log
so a failed finalize is distinguishable from a successful one.

The bare ``[tool-loop-done]`` line tells you the final state, but not whether
the finalize call itself recovered or quietly produced empty text again.
Without a dedicated outcome line, "Kai produced valid JSON" and "Kai's
finalize call also returned 0 chars" look identical in the logs.

These tests pin the contract that a `[tool-loop-finalize-outcome]` line is
emitted after every finalize call, escalated to WARNING when the safety net
itself returns empty text.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from revue_core.core.tool_loop import anthropic_tool_loop, openai_tool_loop
from revue_core.core.tools import ToolResult


# ---------------------------------------------------------------------------
# Helpers — Anthropic
# ---------------------------------------------------------------------------

def _tool_use_block(tool_id: str, name: str, **inputs: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=inputs)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _anth_resp(blocks: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _handler(path: str) -> ToolResult:
    return ToolResult(content=f"contents of {path}", is_error=False)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def test_anthropic_finalize_outcome_logged_on_success(caplog: pytest.LogCaptureFixture) -> None:
    """When the finalize call produces text, an INFO outcome line records the
    text_len and stop_reason. Without this signal, the only evidence that
    finalize ran is the `[tool-loop-finalize]` line *before* it — operators
    can't see whether it actually recovered."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _anth_resp([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _anth_resp([_text_block('{"findings": []}')], "end_turn"),
    ]
    with caplog.at_level(logging.INFO, logger="revue_core.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": _handler},
            max_iterations=1, max_tokens=1024, temperature=0.3,
            system=None, agent_name="kai",
        )
    outcome = [r for r in caplog.records if "tool-loop-finalize-outcome" in r.getMessage()]
    assert outcome, "expected [tool-loop-finalize-outcome] line after a finalize call"
    msg = outcome[0].getMessage()
    assert "kai" in msg
    assert "stop_reason=end_turn" in msg
    assert "text_len=16" in msg


def test_anthropic_finalize_outcome_warns_when_finalize_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the finalize call still returns empty text, the safety net itself
    failed and we MUST surface that as a warning — otherwise operators see
    'agent returned 0 findings' with no hint that finalize even ran. Worst-case
    debugging scenario."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _anth_resp([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        # Finalize attempt: stop_reason=end_turn but no text content at all
        _anth_resp([], "end_turn"),
    ]
    with caplog.at_level(logging.WARNING, logger="revue_core.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": _handler},
            max_iterations=1, max_tokens=1024, temperature=0.3,
            system=None, agent_name="leo",
        )
    warns = [
        r for r in caplog.records
        if "tool-loop-finalize-outcome" in r.getMessage() and r.levelno >= logging.WARNING
    ]
    assert warns, (
        "expected a WARNING when finalize still returns empty text — "
        f"saw {[r.getMessage() for r in caplog.records]}"
    )


def test_anthropic_finalize_outcome_logs_block_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the finalize response carries non-text blocks (thinking, tool_use
    despite no-tools, etc.), the outcome line must include the block type
    summary so we can tell *why* text_len is zero."""
    sdk = MagicMock()
    # Model ignores the no-tools constraint and emits another tool_use block.
    sdk.messages.create.side_effect = [
        _anth_resp([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _anth_resp([_tool_use_block("tu_2", "read_file", path="b.py")], "tool_use"),
    ]
    with caplog.at_level(logging.WARNING, logger="revue_core.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": _handler},
            max_iterations=1, max_tokens=1024, temperature=0.3,
            system=None, agent_name="leo",
        )
    outcome = [r for r in caplog.records if "tool-loop-finalize-outcome" in r.getMessage()]
    assert outcome
    msg = outcome[0].getMessage()
    # Must mention block types so we can see "tool_use returned despite no-tools"
    assert "tool_use" in msg, f"expected block shape in outcome line: {msg}"


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def _openai_msg(content: "str | None", tool_calls: "list | None" = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _openai_choice(message: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(message=message)


def _openai_resp(choices: list, usage: "Any | None" = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=choices,
        usage=usage or SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
        ),
    )


def _openai_tc(tc_id: str, name: str, args_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=args_json),
    )


def test_openai_finalize_outcome_logged_on_success(caplog: pytest.LogCaptureFixture) -> None:
    """Symmetric requirement on the OpenAI-compatible path."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _openai_resp([_openai_choice(_openai_msg(
            None, tool_calls=[_openai_tc("tc_1", "read_file", '{"path":"a.py"}')]
        ))]),
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]
    with caplog.at_level(logging.INFO, logger="revue_core.core.tool_loop"):
        openai_tool_loop(
            sdk, model="gpt-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": _handler},
            max_iterations=1, max_tokens=1024, temperature=0.3,
            system=None, provider_label="openai", agent_name="kai",
        )
    outcome = [r for r in caplog.records if "tool-loop-finalize-outcome" in r.getMessage()]
    assert outcome
    msg = outcome[0].getMessage()
    assert "kai" in msg
    assert "text_len=16" in msg


def test_openai_finalize_outcome_warns_when_finalize_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even on the OpenAI side, an empty post-finalize response must escalate
    to WARNING so the silent-failure mode the user worried about can't hide."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _openai_resp([_openai_choice(_openai_msg(
            None, tool_calls=[_openai_tc("tc_1", "read_file", '{"path":"a.py"}')]
        ))]),
        # Empty content on finalize attempt
        _openai_resp([_openai_choice(_openai_msg(None))]),
    ]
    with caplog.at_level(logging.WARNING, logger="revue_core.core.tool_loop"):
        openai_tool_loop(
            sdk, model="gpt-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": _handler},
            max_iterations=1, max_tokens=1024, temperature=0.3,
            system=None, provider_label="openai", agent_name="leo",
        )
    warns = [
        r for r in caplog.records
        if "tool-loop-finalize-outcome" in r.getMessage() and r.levelno >= logging.WARNING
    ]
    assert warns, "expected WARNING when OpenAI finalize returns empty content"

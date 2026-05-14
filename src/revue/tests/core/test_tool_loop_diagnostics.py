"""REVUE-241 diagnostics: tool_loop emits the signals needed to debug
tool-use response-format regressions.

Why this matters: when an agent reads files via read_file and then returns
prose instead of the requested JSON, the operator needs to know:
  * how many iterations did the loop run before terminating?
  * what was the final stop_reason — end_turn (model finished) or
    max_iterations (we cut it off mid-tool-use)?
  * was the final response empty (cut off mid-thought)?

Without these signals, "agent returned prose" is indistinguishable from
"agent never got a chance to emit JSON because the loop ran out of turns".
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from revue.core.tool_loop import anthropic_tool_loop
from revue.core.tools import ToolResult


def _tool_use_block(tool_id: str, name: str, **inputs: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=inputs)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _fake_response(blocks: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def test_tool_loop_logs_stop_reason_when_loop_ends(caplog: pytest.LogCaptureFixture) -> None:
    """The loop emits a [tool-loop-done] line on termination with stop_reason
    and iteration count. Tells operators 'agent ended cleanly at iter N'."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _fake_response([_text_block('[{"file":"a.py","line":1}]')], "end_turn"),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

    with caplog.at_level(logging.INFO, logger="revue.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": handler},
            max_iterations=5, max_tokens=1024, temperature=0.3,
            system=None, agent_name="maya",
        )

    done_records = [r for r in caplog.records if "tool-loop-done" in r.getMessage()]
    assert done_records, "expected a [tool-loop-done] line when the loop terminates"
    msg = done_records[0].getMessage()
    assert "maya" in msg
    assert "stop_reason=end_turn" in msg, f"final stop_reason missing: {msg}"
    assert "iterations=2" in msg, f"iteration count missing: {msg}"


def test_tool_loop_logs_warning_when_max_iterations_hit(caplog: pytest.LogCaptureFixture) -> None:
    """When the loop exhausts max_iterations without the model producing a
    final answer, emit a WARNING — silent termination would mask the real
    cause of an 'agent returned nothing' outcome."""
    sdk = MagicMock()
    # Every iteration returns another tool_use → never reaches end_turn.
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block(f"tu_{i}", "read_file", path=f"f{i}.py")], "tool_use")
        for i in range(10)
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content="x", is_error=False)

    with caplog.at_level(logging.WARNING, logger="revue.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": handler},
            max_iterations=3, max_tokens=1024, temperature=0.3,
            system=None, agent_name="leo",
        )

    warn_records = [
        r for r in caplog.records
        if "tool-loop-max-iterations" in r.getMessage() and r.levelno == logging.WARNING
    ]
    assert warn_records, (
        "expected a WARNING when max_iterations is exhausted mid-tool-use — "
        "silent termination here is how an 'agent returned nothing' becomes "
        "indistinguishable from 'agent ran out of turns'"
    )
    assert "leo" in warn_records[0].getMessage()


def test_tool_loop_logs_iteration_start_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """At DEBUG level the loop emits an iteration marker before each
    messages.create call. Lets operators correlate API calls with cost/latency
    when REVUE_LOG_LEVEL=DEBUG is enabled."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_text_block('[]')], "end_turn"),
    ]

    with caplog.at_level(logging.DEBUG, logger="revue.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={},
            max_iterations=5, max_tokens=1024, temperature=0.3,
            system=None, agent_name="zara",
        )

    iter_records = [r for r in caplog.records if "tool-loop-iter" in r.getMessage()]
    assert iter_records, "expected DEBUG [tool-loop-iter] markers per iteration"
    assert "iter=1" in iter_records[0].getMessage()

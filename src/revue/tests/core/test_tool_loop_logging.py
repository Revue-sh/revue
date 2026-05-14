"""REVUE-241: per-tool-invocation logging in anthropic_tool_loop.

The whole point of REVUE-241 is to give reviewer agents lazy file-read access
so they stop hallucinating findings against code they can't see. The only
empirical way to confirm an agent actually used the tool — vs the diff alone —
is to log every tool dispatch with agent_name + tool name + path. Without this
log line, a "0 findings" outcome could be tool-driven OR prompt-tuning-driven
and we can't tell which.
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


def test_anthropic_tool_loop_logs_each_tool_invocation(caplog: pytest.LogCaptureFixture) -> None:
    """Every dispatched tool_use block emits an INFO line tagged with agent_name,
    tool name, and the input arguments. This is the only externally-visible
    evidence that an agent actually called read_file rather than relying on diff
    context alone."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="src/app.py")], "tool_use"),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

    with caplog.at_level(logging.INFO, logger="revue.core.tool_loop"):
        anthropic_tool_loop(
            sdk,
            model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": handler},
            max_iterations=5,
            max_tokens=1024,
            temperature=0.3,
            system=None,
            agent_name="maya",
        )

    matching = [r for r in caplog.records if "tool-call" in r.getMessage()]
    assert matching, "expected a [tool-call] log line per tool_use block"
    msg = matching[0].getMessage()
    assert "maya" in msg, f"log must name the calling agent; got: {msg}"
    assert "read_file" in msg, f"log must name the tool; got: {msg}"
    assert "src/app.py" in msg, f"log must include input args (path); got: {msg}"


def test_anthropic_tool_loop_logs_one_line_per_tool_use_block(caplog: pytest.LogCaptureFixture) -> None:
    """A single turn with 3 tool_use blocks emits 3 [tool-call] lines — one per
    invocation. Coalescing into a single summary line would hide which file each
    agent actually read."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response(
            [
                _tool_use_block("tu_a", "read_file", path="a.py"),
                _tool_use_block("tu_b", "read_file", path="b.py"),
                _tool_use_block("tu_c", "read_file", path="c.py"),
            ],
            "tool_use",
        ),
        _fake_response([_text_block("done")], "end_turn"),
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
            system=None, agent_name="leo",
        )

    matching = [r for r in caplog.records if "tool-call" in r.getMessage()]
    assert len(matching) == 3, (
        f"expected 3 [tool-call] log lines (one per tool_use block); got {len(matching)}: "
        f"{[r.getMessage() for r in matching]}"
    )
    paths_seen = "".join(r.getMessage() for r in matching)
    for path in ("a.py", "b.py", "c.py"):
        assert path in paths_seen, f"path {path!r} missing from tool-call logs"


def test_anthropic_tool_loop_logs_tool_errors_distinctly(caplog: pytest.LogCaptureFixture) -> None:
    """A tool that returns is_error=True is logged at WARNING with a tag that
    distinguishes failures from successes — so operators can grep the rejection
    rate from logs alone."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="ghost.py")], "tool_use"),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content="file not in allowed_paths", is_error=True)

    with caplog.at_level(logging.WARNING, logger="revue.core.tool_loop"):
        anthropic_tool_loop(
            sdk, model="claude-x",
            messages=[{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
            tool_handlers={"read_file": handler},
            max_iterations=5, max_tokens=1024, temperature=0.3,
            system=None, agent_name="zara",
        )

    err_records = [r for r in caplog.records if "tool-call-error" in r.getMessage()]
    assert err_records, "expected a [tool-call-error] WARNING line on is_error=True"
    assert err_records[0].levelno == logging.WARNING
    assert "ghost.py" in err_records[0].getMessage()

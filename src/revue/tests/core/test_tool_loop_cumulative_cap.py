"""Tests for the cumulative tool-result cap in anthropic_tool_loop — REVUE-243 AC3.

When the total byte size of tool_result content blocks in current_messages
exceeds a configurable threshold, the loop must skip the next tool iteration
and go directly to the no-tools finalize path. The review **succeeds** (emits
findings from what was retrieved) rather than failing with `prompt is too long`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from revue.core.tool_loop import anthropic_tool_loop
from revue.core.tools import ToolResult


def _make_tool_use_resp(tool_name: str, tool_input: dict) -> SimpleNamespace:
    """Mock an Anthropic response carrying one tool_use content block."""
    block = SimpleNamespace(
        type="tool_use",
        id="tu_test",
        name=tool_name,
        input=tool_input,
    )
    return SimpleNamespace(
        content=[block],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _make_final_resp(text: str) -> SimpleNamespace:
    """Mock a final assistant response with no tool calls."""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(
        content=[block],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def test_tool_loop_force_finalizes_when_cumulative_tool_results_exceed_threshold() -> None:
    """A reviewer that fills 3 iterations with ~30 KB tool_results each
    (cumulative ~90 KB) must hit the cap (default 80_000 bytes) and skip
    further tool iterations — the loop short-circuits to the no-tools
    finalize call, returning real findings instead of crashing."""
    # Arrange — handler returns a 30 KB tool result so 3 iterations exceed the cap
    big_content = "x" * 30_000

    def fake_handler(**kwargs):
        return ToolResult(content=big_content, is_error=False)

    # Mock SDK: first 3 calls request tool_use; 4th (the no-tools finalize) returns text
    mock_sdk = MagicMock()
    mock_sdk.messages.create.side_effect = [
        _make_tool_use_resp("read_file", {"path": "a.py"}),
        _make_tool_use_resp("read_file", {"path": "b.py"}),
        _make_tool_use_resp("read_file", {"path": "c.py"}),
        _make_final_resp('{"findings": []}'),
    ]

    # Act
    result = anthropic_tool_loop(
        mock_sdk,
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file"}],
        tool_handlers={"read_file": fake_handler},
        max_iterations=5,
        max_tokens=4096,
        temperature=0.3,
        system=None,
        agent_name="test-agent",
    )

    # Assert — finalize was invoked (the last call has no `tools` kwarg)
    assert result.text == '{"findings": []}'
    final_call_kwargs = mock_sdk.messages.create.call_args_list[-1].kwargs
    assert "tools" not in final_call_kwargs, (
        "AC3: when the cumulative cap fires the loop must call without tools "
        "so the agent emits findings instead of more tool_use blocks."
    )
    # And the loop did NOT keep calling tool iterations past the cap
    assert mock_sdk.messages.create.call_count == 4, (
        "Expected 3 tool-use iterations then 1 finalize call, "
        f"got {mock_sdk.messages.create.call_count}"
    )


def test_tool_loop_cumulative_cap_is_configurable() -> None:
    """The cap threshold must be tunable so we can ratchet it up or down without
    changing the loop code. Pass cap=40_000; cap fires on iteration 2 instead of 3."""
    # Arrange — 30 KB per result; cap=40_000 means iteration 2 trips it
    def fake_handler(**kwargs):
        return ToolResult(content="x" * 30_000, is_error=False)

    mock_sdk = MagicMock()
    mock_sdk.messages.create.side_effect = [
        _make_tool_use_resp("read_file", {"path": "a.py"}),
        _make_tool_use_resp("read_file", {"path": "b.py"}),
        _make_final_resp('{"findings": []}'),
    ]

    # Act
    result = anthropic_tool_loop(
        mock_sdk,
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file"}],
        tool_handlers={"read_file": fake_handler},
        max_iterations=5,
        max_tokens=4096,
        temperature=0.3,
        system=None,
        agent_name="test-agent",
        tool_result_bytes_cap=40_000,
    )

    # Assert — fired earlier: 2 tool iterations + 1 finalize
    assert mock_sdk.messages.create.call_count == 3
    final_call_kwargs = mock_sdk.messages.create.call_args_list[-1].kwargs
    assert "tools" not in final_call_kwargs


def test_tool_loop_does_not_finalize_early_when_cumulative_results_under_cap() -> None:
    """Sanity: a small reviewer whose tool_results stay under the cap completes
    via the normal stop_reason='end_turn' path, not the finalize path."""
    # Arrange — tiny results
    def fake_handler(**kwargs):
        return ToolResult(content="small", is_error=False)

    mock_sdk = MagicMock()
    mock_sdk.messages.create.side_effect = [
        _make_tool_use_resp("read_file", {"path": "a.py"}),
        _make_final_resp('{"findings": []}'),
    ]

    # Act
    result = anthropic_tool_loop(
        mock_sdk,
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file"}],
        tool_handlers={"read_file": fake_handler},
        max_iterations=5,
        max_tokens=4096,
        temperature=0.3,
        system=None,
        agent_name="test-agent",
    )

    # Assert — only 2 calls total; both went through the tools-enabled path
    assert result.text == '{"findings": []}'
    assert mock_sdk.messages.create.call_count == 2
    # Final call STILL has tools (it's the normal end_turn, not forced finalize)
    final_call_kwargs = mock_sdk.messages.create.call_args_list[-1].kwargs
    assert "tools" in final_call_kwargs

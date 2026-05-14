"""REVUE-241 D2: tool_result blocks carry cache_control: ephemeral.

Why this matters: when a reviewer agent (or Nova/Vex) reads a file via the
``read_file`` tool, the file's content is appended to the messages history.
On the next iteration of the tool-use loop — or on a re-run against the same
diff — the Anthropic prompt cache reads the content at 0.1× input cost
instead of paying the full token price.

Without cache_control, file reads burn full input cost on every iteration,
inflating PR-review token bills well beyond the ADR's cost projection.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

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
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


def test_successful_tool_result_carries_cache_control_ephemeral_1h_ttl() -> None:
    """A successful tool_result block includes cache_control: ephemeral with
    an explicit 1h TTL — NOT bare ``{"type": "ephemeral", "ttl": "1h"}``.

    Why this matters: Anthropic changed the ephemeral default from 1h to 5m
    on 2026-03-06. The bare form now buys a 5-minute cache window, which
    expires before the next dogfood iteration on a developer's local loop
    and wastes the cache_write cost. The codebase convention everywhere
    else (see ``_CACHE_CONTROL_1H`` in ai_client.py) is explicit 1h TTL —
    tool_result must match so the per-file cache prefix outlives a single
    review cycle.
    """
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def read_file_handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

    anthropic_tool_loop(
        sdk,
        model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": read_file_handler},
        max_iterations=5,
        max_tokens=1024,
        temperature=0.3,
        system=None,
    )

    second_call_messages = sdk.messages.create.call_args_list[1][1]["messages"]
    tool_result_user_msg = second_call_messages[-1]
    assert tool_result_user_msg["role"] == "user"
    blocks = tool_result_user_msg["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}, (
        "tool_result cache_control must specify ttl=1h to match the codebase "
        "convention (see _CACHE_CONTROL_1H in ai_client.py). Bare "
        '{"type": "ephemeral", "ttl": "1h"} now means 5m TTL since 2026-03-06 and the '
        "cache expires between dogfood iterations, wasting the write cost."
    )


def test_error_tool_result_does_not_carry_cache_control() -> None:
    """Error tool_results are transient and must NOT pin a cache prefix."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="ghost.py")], "tool_use"),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def read_file_handler(path: str) -> ToolResult:
        return ToolResult(content="file not found", is_error=True)

    anthropic_tool_loop(
        sdk,
        model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": read_file_handler},
        max_iterations=5,
        max_tokens=1024,
        temperature=0.3,
        system=None,
    )

    second_call_messages = sdk.messages.create.call_args_list[1][1]["messages"]
    blocks = second_call_messages[-1]["content"]
    assert blocks[0]["is_error"] is True
    assert "cache_control" not in blocks[0]


def test_only_last_successful_tool_result_carries_cache_control_per_turn() -> None:
    """Anthropic permits at most 4 cache_control breakpoints per request.

    With multiple successful tool_use blocks in a single turn, marking every
    successful tool_result would burn a breakpoint each. Only the *last*
    successful tool_result in the turn anchors the cache prefix; earlier ones
    are within the cached span by virtue of position.
    """
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response(
            [
                _tool_use_block("tu_first", "read_file", path="a.py"),
                _tool_use_block("tu_second", "read_file", path="b.py"),
                _tool_use_block("tu_third", "read_file", path="c.py"),
            ],
            "tool_use",
        ),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

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
    )

    second_call_messages = sdk.messages.create.call_args_list[1][1]["messages"]
    blocks = second_call_messages[-1]["content"]
    by_id = {b["tool_use_id"]: b for b in blocks}
    assert "cache_control" not in by_id["tu_first"]
    assert "cache_control" not in by_id["tu_second"]
    assert by_id["tu_third"]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_prior_turn_tool_results_lose_cache_control() -> None:
    """Across iterations, only the latest turn's last successful tool_result
    keeps cache_control. Prior turns' breakpoints are stripped so the request
    never exceeds Anthropic's 4-breakpoint cap as the conversation grows.
    """
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _fake_response([_tool_use_block("tu_2", "read_file", path="b.py")], "tool_use"),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

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
    )

    third_call_messages = sdk.messages.create.call_args_list[2][1]["messages"]
    # Collect every user-role tool_result block across the conversation.
    tool_result_blocks: list[dict] = []
    for msg in third_call_messages:
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tool_result_blocks.append(b)
    cached = [b for b in tool_result_blocks if "cache_control" in b]
    assert len(cached) == 1, "exactly one cache breakpoint should survive across iterations"
    assert cached[0]["tool_use_id"] == "tu_2", "only the latest turn's tool_result is cached"


def test_mixed_results_only_successful_blocks_get_cache_control() -> None:
    """When a turn produces both a success and an error tool_result, only the
    last successful one carries cache_control."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response(
            [
                _tool_use_block("tu_ok", "read_file", path="a.py"),
                _tool_use_block("tu_bad", "read_file", path="missing.py"),
            ],
            "tool_use",
        ),
        _fake_response([_text_block("done")], "end_turn"),
    ]

    def read_file_handler(path: str) -> ToolResult:
        if path == "a.py":
            return ToolResult(content="contents", is_error=False)
        return ToolResult(content="missing", is_error=True)

    anthropic_tool_loop(
        sdk,
        model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": read_file_handler},
        max_iterations=5,
        max_tokens=1024,
        temperature=0.3,
        system=None,
    )

    second_call_messages = sdk.messages.create.call_args_list[1][1]["messages"]
    blocks = second_call_messages[-1]["content"]
    by_id = {b["tool_use_id"]: b for b in blocks}
    assert by_id["tu_ok"]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in by_id["tu_bad"]

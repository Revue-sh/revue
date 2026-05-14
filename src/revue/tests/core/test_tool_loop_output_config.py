"""REVUE-241: anthropic_tool_loop forwards output_config to messages.create.

Grammar-constrained final responses are the fix for the prose-drift
regression observed when reviewer agents use the read_file tool — without
``output_config``, the model emits "Based on my analysis..." prose after
the last tool round and the consolidator silently treats parse failures as
0 findings.

Per Anthropic's docs, the grammar applies ONLY to the model's direct text
output, not to tool_use blocks or tool_result content — so the loop's
existing iteration logic stays correct; the kwarg just needs to ride along
on every messages.create call.
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
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
}


def test_output_config_passed_on_first_call() -> None:
    """When ``output_config`` is provided, the very first messages.create
    call carries it — the grammar must be in effect from turn one or the
    cache compilation can't be shared across iterations."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_text_block('{"findings": []}')], "end_turn"),
    ]

    anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, agent_name="maya",
        output_config=_OUTPUT_CONFIG,
    )

    call_kwargs = sdk.messages.create.call_args_list[0][1]
    assert call_kwargs.get("output_config") == _OUTPUT_CONFIG


def test_output_config_passed_on_every_iteration() -> None:
    """The kwarg rides along on every messages.create call within the loop
    — not just the first. Anthropic caches the compiled grammar; passing it
    on every call lets every iteration hit that cache."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _fake_response([_tool_use_block("tu_2", "read_file", path="b.py")], "tool_use"),
        _fake_response([_text_block('{"findings": []}')], "end_turn"),
    ]

    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

    anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, agent_name="leo",
        output_config=_OUTPUT_CONFIG,
    )

    assert sdk.messages.create.call_count == 3
    for idx, call in enumerate(sdk.messages.create.call_args_list):
        assert call[1].get("output_config") == _OUTPUT_CONFIG, (
            f"output_config missing from messages.create call #{idx + 1}"
        )


def test_output_config_omitted_when_not_supplied() -> None:
    """When the caller does not pass output_config (back-compat for Nova /
    Vex / other callers that don't need a grammar), the kwarg is omitted —
    NOT passed as None — so the SDK doesn't reject it."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _fake_response([_text_block("free text")], "end_turn"),
    ]

    anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "synthesise"}],
        tools=[],
        tool_handlers={},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, agent_name="nova",
    )

    call_kwargs = sdk.messages.create.call_args_list[0][1]
    assert "output_config" not in call_kwargs, (
        "output_config must not be passed when caller didn't supply it — "
        "passing None could be rejected by the SDK or invalidate cache "
        "compilation"
    )

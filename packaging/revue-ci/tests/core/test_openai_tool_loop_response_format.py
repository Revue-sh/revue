"""REVUE-241: openai_tool_loop forwards response_format to chat.completions.create.

Mirror of the Anthropic ``output_config`` wiring on the OpenAI-compatible
path. With this in place a single reviewer-level kwarg (output_config)
travels:

  agent_loader → AnthropicClient.complete_with_tools → anthropic_tool_loop
  agent_loader → OpenAI* Client.complete_with_tools  → openai_tool_loop

The two loops speak different parameter names (output_config vs
response_format) but both grammar-constrain the model's final text after
multi-turn tool use — the fix for the prose-drift bug on either provider.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from revue_core.core.tool_loop import openai_tool_loop


def _msg(content: "str | None", tool_calls: "list | None" = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _choice(message: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(message=message)


def _resp(choices: list, usage: "Any | None" = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=choices,
        usage=usage or SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
        ),
    )


_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "findings_response",
        "strict": True,
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def test_response_format_passed_on_first_call() -> None:
    """When response_format is provided, the first chat.completions.create
    carries it — same contract as the Anthropic side."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _resp([_choice(_msg('{"findings": []}'))]),
    ]

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        response_format=_RESPONSE_FORMAT,
    )

    call_kwargs = sdk.chat.completions.create.call_args_list[0][1]
    assert call_kwargs.get("response_format") == _RESPONSE_FORMAT


def test_response_format_passed_on_every_iteration() -> None:
    """response_format must ride along on every loop iteration so cached
    grammar artifacts (where backends support them) stay warm."""
    sdk = MagicMock()
    tool_call = SimpleNamespace(
        id="tc_1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"a.py"}'),
    )
    sdk.chat.completions.create.side_effect = [
        _resp([_choice(_msg(None, tool_calls=[tool_call]))]),
        _resp([_choice(_msg('{"findings": []}'))]),
    ]

    from revue_core.core.tools import ToolResult
    def handler(path: str) -> ToolResult:
        return ToolResult(content=f"contents of {path}", is_error=False)

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
        response_format=_RESPONSE_FORMAT,
    )

    assert sdk.chat.completions.create.call_count == 2
    for idx, call in enumerate(sdk.chat.completions.create.call_args_list):
        assert call[1].get("response_format") == _RESPONSE_FORMAT, (
            f"response_format missing from chat.completions.create call #{idx + 1}"
        )


def test_response_format_omitted_when_not_supplied() -> None:
    """Without response_format, the kwarg is not passed — the SDK shouldn't
    receive an explicit None and existing callers (Nova/Vex on the OpenAI
    path) must not see new keyword behavior."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _resp([_choice(_msg("free text"))]),
    ]

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "go"}],
        tools=[],
        tool_handlers={},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
    )

    assert "response_format" not in sdk.chat.completions.create.call_args_list[0][1]

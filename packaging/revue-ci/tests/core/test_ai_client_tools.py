"""Tests for the complete_with_tools tool-use loop (REVUE-239).

Anthropic and OpenAI tool-use formats differ on the wire, but the public
``complete_with_tools(messages, tools=[...], tool_handlers={...})`` API is the
same for all clients. Tests verify:
  - Single-turn (no tool call) → returns text immediately.
  - Multi-turn (tool_use → tool_result → final) → invokes handler, returns text.
  - Tool definitions are forwarded to the provider API.
  - Iteration cap stops runaway loops.
  - Unknown tool name returns an error tool_result instead of raising.

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from revue_core.core.ai_client import AnthropicClient, OpenAIClient
from revue_core.core.ai_config import AIConfig
from revue_core.core.tools import ToolResult


# ---------------------------------------------------------------------------
# Helpers — match the existing test file's _make_config pattern
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> AIConfig:
    defaults: dict[str, Any] = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="glpat-test",
        gitlab_project_id="42",
        gitlab_project_path="org/repo",
        gitlab_project_url="https://gitlab.example.com/org/repo",
        genai_gateway_url="https://gateway.example.com/v1",
        openai_api_key="sk-test",
        gen_ai_gateway_model="claude-sonnet-4-5-20250929",
        ai_temp=0.3,
        ai_confidence=70,
        ai_max_tokens=50000,
        provider="anthropic",
        api_key="sk-test",
        api_key_env="",
        base_url="",
        model="claude-haiku-4-5-20251001",
        azure_endpoint="",
        azure_deployment="",
        azure_api_version="2024-02-01",
    )
    defaults.update(overrides)
    return AIConfig(**defaults)


_READ_FILE_TOOL_DEF: dict[str, Any] = {
    "name": "read_file",
    "description": "Read a PR file.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}


def _anthropic_text_response(text: str, *, stop_reason: str = "end_turn") -> MagicMock:
    """Build a MagicMock that mimics anthropic.types.Message with a text block."""
    msg = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    msg.content = [text_block]
    msg.stop_reason = stop_reason
    msg.usage = MagicMock(
        input_tokens=100, output_tokens=20,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return msg


def _anthropic_tool_use_response(*, tool_name: str, tool_input: dict, tool_use_id: str) -> MagicMock:
    """Build a MagicMock mimicking anthropic.types.Message with a tool_use block."""
    msg = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_use_id
    msg.content = [block]
    msg.stop_reason = "tool_use"
    msg.usage = MagicMock(
        input_tokens=150, output_tokens=30,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return msg


# ---------------------------------------------------------------------------
# Anthropic — no tool use path
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_with_tools_returns_text_when_no_tool_use(
    mock_anthropic_cls: MagicMock,
) -> None:
    """Single-turn end_turn response: tool loop exits immediately and returns the text."""
    # Arrange
    mock_anthropic_cls.return_value.messages.create.return_value = (
        _anthropic_text_response("no tools needed")
    )
    client = AnthropicClient(_make_config(provider="anthropic"))
    handler = MagicMock()  # never called

    # Act
    result = client.complete_with_tools(
        messages=[{"role": "user", "content": "consolidate findings"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": handler},
    )

    # Assert
    assert result.text == "no tools needed"
    handler.assert_not_called()
    assert mock_anthropic_cls.return_value.messages.create.call_count == 1


# ---------------------------------------------------------------------------
# Anthropic — single tool call then final text
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_with_tools_invokes_handler_and_returns_final_text(
    mock_anthropic_cls: MagicMock,
) -> None:
    """tool_use → tool_result → end_turn: handler invoked with parsed input, final text returned."""
    # Arrange — first response asks for tool, second is final
    mock_anthropic_cls.return_value.messages.create.side_effect = [
        _anthropic_tool_use_response(
            tool_name="read_file",
            tool_input={"path": "src/example.py"},
            tool_use_id="toolu_abc123",
        ),
        _anthropic_text_response('[{"file":"src/example.py","line":42}]'),
    ]
    client = AnthropicClient(_make_config(provider="anthropic"))
    handler = MagicMock(return_value=ToolResult(content="def foo():\n    return 1\n"))

    # Act
    result = client.complete_with_tools(
        messages=[{"role": "user", "content": "consolidate findings on src/example.py"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": handler},
    )

    # Assert — handler invoked with parsed input
    handler.assert_called_once_with(path="src/example.py")
    # Final text returned (the JSON Nova produced after seeing the file)
    assert result.text == '[{"file":"src/example.py","line":42}]'
    # Two API calls were made (tool_use then final)
    assert mock_anthropic_cls.return_value.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# Anthropic — tool definitions forwarded to API
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_with_tools_forwards_tool_definitions_to_api(
    mock_anthropic_cls: MagicMock,
) -> None:
    """The tools= list is passed verbatim to messages.create on first call."""
    # Arrange
    mock_anthropic_cls.return_value.messages.create.return_value = (
        _anthropic_text_response("done")
    )
    client = AnthropicClient(_make_config(provider="anthropic"))

    # Act
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert
    first_call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args_list[0].kwargs
    assert first_call_kwargs["tools"] == [_READ_FILE_TOOL_DEF]


# ---------------------------------------------------------------------------
# Anthropic — iteration cap stops runaway loops
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_with_tools_stops_at_max_iterations(
    mock_anthropic_cls: MagicMock,
) -> None:
    """Model keeps asking for tool calls forever: loop stops at max_iterations
    and then issues the REVUE-241 Gap 2 forced-finalize call (no tools) so the
    model surfaces a final text response instead of empty output.

    Result is the finalize call's text — empty here because the test mock returns
    tool_use every time, including for the finalize attempt; in production the
    finalize call has no tools defined so the model can only return text.
    """
    # Arrange — model never stops requesting tool calls. The forced-finalize
    # call itself goes without tools, so the fact that the mock keeps returning
    # tool_use just means we drain one extra response (the finalize attempt).
    mock_anthropic_cls.return_value.messages.create.side_effect = [
        _anthropic_tool_use_response(
            tool_name="read_file",
            tool_input={"path": "src/loop.py"},
            tool_use_id=f"toolu_{i}",
        )
        for i in range(10)
    ]
    client = AnthropicClient(_make_config(provider="anthropic"))
    handler = MagicMock(return_value=ToolResult(content="file content"))

    # Act
    result = client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": handler},
        max_iterations=3,
    )

    # 3 loop iterations + 1 forced-finalize call = 4 API calls. The handler
    # still fires 3 times (only the loop iterations invoke tools; the finalize
    # call passes no tools at all).
    assert mock_anthropic_cls.return_value.messages.create.call_count == 4
    assert handler.call_count == 3
    # The finalize call was NOT given `tools` — verify by inspecting kwargs
    # on the last invocation.
    last_call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args_list[-1][1]
    assert last_call_kwargs.get("tools") in (None, []), (
        f"finalize call must not pass tools; got {last_call_kwargs.get('tools')!r}"
    )
    # Empty text is acceptable here: the mock returns tool_use even for the
    # finalize call. In production, omitting `tools` forces the model to
    # respond with text rather than another tool_use.
    assert result.text == ""


# ---------------------------------------------------------------------------
# Anthropic — unknown tool name returns error to model, doesn't crash
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.anthropic.Anthropic")
def test_anthropic_complete_with_tools_returns_error_for_unknown_tool_name(
    mock_anthropic_cls: MagicMock,
) -> None:
    """Model calls a tool not in tool_handlers: loop sends back is_error=True tool_result."""
    # Arrange — model asks for unknown tool, then responds with text
    mock_anthropic_cls.return_value.messages.create.side_effect = [
        _anthropic_tool_use_response(
            tool_name="delete_database",
            tool_input={"confirm": True},
            tool_use_id="toolu_evil",
        ),
        _anthropic_text_response("ok, no tool used"),
    ]
    client = AnthropicClient(_make_config(provider="anthropic"))
    handler = MagicMock()  # not for delete_database

    # Act
    result = client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": handler},
    )

    # Assert — handler not called (no read_file requested)
    handler.assert_not_called()
    # Loop continued and returned final text
    assert result.text == "ok, no tool used"
    # Second call's messages must contain a tool_result with is_error=True for delete_database
    second_call_kwargs = mock_anthropic_cls.return_value.messages.create.call_args_list[1].kwargs
    messages_sent = second_call_kwargs["messages"]
    last_user_msg = messages_sent[-1]
    assert last_user_msg["role"] == "user"
    tool_result_block = last_user_msg["content"][0]
    assert tool_result_block["type"] == "tool_result"
    assert tool_result_block["tool_use_id"] == "toolu_evil"
    assert tool_result_block.get("is_error") is True


# ---------------------------------------------------------------------------
# OpenAI — single round-trip (no tool call)
# ---------------------------------------------------------------------------


def _openai_text_response(text: str) -> MagicMock:
    """MagicMock mimicking openai.types.ChatCompletion with a single text choice."""
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=100, completion_tokens=20)
    return resp


def _openai_tool_call_response(*, tool_name: str, tool_input_json: str, tool_call_id: str) -> MagicMock:
    """MagicMock mimicking openai response with finish_reason='tool_calls'."""
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = None
    tool_call = MagicMock()
    tool_call.id = tool_call_id
    tool_call.type = "function"
    tool_call.function.name = tool_name
    tool_call.function.arguments = tool_input_json
    choice.message.tool_calls = [tool_call]
    choice.message.role = "assistant"
    choice.finish_reason = "tool_calls"
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=150, completion_tokens=30)
    return resp


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_with_tools_returns_text_when_no_tool_call(
    mock_openai_cls: MagicMock,
) -> None:
    """OpenAI: single-turn 'stop' finish_reason returns text without invoking any handler."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("no tools needed")
    )
    client = OpenAIClient(_make_config(provider="openai"))
    handler = MagicMock()

    # Act
    result = client.complete_with_tools(
        messages=[{"role": "user", "content": "consolidate"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": handler},
    )

    # Assert
    assert result.text == "no tools needed"
    handler.assert_not_called()
    assert mock_openai_cls.return_value.chat.completions.create.call_count == 1


# ---------------------------------------------------------------------------
# OpenAI — tool_calls then text
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_with_tools_invokes_handler_and_returns_final_text(
    mock_openai_cls: MagicMock,
) -> None:
    """OpenAI: tool_calls finish_reason → handler invoked → loop continues → final text."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.side_effect = [
        _openai_tool_call_response(
            tool_name="read_file",
            tool_input_json='{"path": "src/example.py"}',
            tool_call_id="call_abc",
        ),
        _openai_text_response('[{"file":"src/example.py","line":42}]'),
    ]
    client = OpenAIClient(_make_config(provider="openai"))
    handler = MagicMock(return_value=ToolResult(content="def foo():\n    return 1\n"))

    # Act
    result = client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": handler},
    )

    # Assert — handler called with parsed kwargs
    handler.assert_called_once_with(path="src/example.py")
    assert result.text == '[{"file":"src/example.py","line":42}]'
    assert mock_openai_cls.return_value.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# OpenAI — tool definitions translated to OpenAI function-tool format
# ---------------------------------------------------------------------------


@patch("revue_core.core.ai_client.openai.OpenAI")
def test_openai_complete_with_tools_translates_tool_def_to_openai_format(
    mock_openai_cls: MagicMock,
) -> None:
    """Anthropic-style tool def is translated to OpenAI's {type: 'function', function: {...}} format."""
    # Arrange
    mock_openai_cls.return_value.chat.completions.create.return_value = (
        _openai_text_response("done")
    )
    client = OpenAIClient(_make_config(provider="openai"))

    # Act
    client.complete_with_tools(
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
    )

    # Assert — tools= kwarg uses OpenAI's function-tool envelope
    first_call_kwargs = mock_openai_cls.return_value.chat.completions.create.call_args_list[0].kwargs
    openai_tools = first_call_kwargs["tools"]
    assert len(openai_tools) == 1
    assert openai_tools[0]["type"] == "function"
    assert openai_tools[0]["function"]["name"] == "read_file"
    assert openai_tools[0]["function"]["parameters"] == _READ_FILE_TOOL_DEF["input_schema"]

"""REVUE-263: per-model ``tool_choice_first_turn`` knob in ``openai_tool_loop``.

Some OpenAI-compatible models (qwen3-coder-next, deepseek-v4-pro) only
reliably call tools when nudged with ``tool_choice="required"`` on the
opening turn — leaving the default ``auto`` produces 0-finding runs even
when the diff contains obvious issues. REVUE-263 wires the registry's
``tool_choice_first_turn`` knob through so each model gets the value its
provider needs.

These tests pin the loop-level contract:
- Turn 1 injects the requested ``tool_choice`` into ``chat.completions.create``.
- Subsequent turns omit it (OpenAI defaults the missing key to ``"auto"``).
- The default (no kwarg supplied) is still ``"auto"`` for back-compat.
- Invalid values are rejected with ``ValueError`` before any API call.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from revue_core.core.tool_loop import openai_tool_loop


# ---------------------------------------------------------------------------
# Helpers — mirror the response shape used elsewhere in the test suite
# ---------------------------------------------------------------------------


def _openai_text_response(text: str) -> MagicMock:
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


def _openai_tool_call_response(
    *, tool_name: str, tool_input_json: str, tool_call_id: str
) -> MagicMock:
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = None
    tool_call = MagicMock()
    tool_call.id = tool_call_id
    tool_call.type = "function"
    tool_call.function.name = tool_name
    tool_call.function.arguments = tool_input_json
    choice.message.tool_calls = [tool_call]
    choice.finish_reason = "tool_calls"
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


_READ_FILE_TOOL_DEF: dict[str, Any] = {
    "name": "read_file",
    "description": "Read a PR file.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}


def _invoke(sdk: Any, **overrides: Any) -> Any:
    """Call ``openai_tool_loop`` with sensible defaults; overrides win."""
    kwargs: dict[str, Any] = dict(
        sdk_client=sdk,
        model="any-model",
        messages=[{"role": "user", "content": "x"}],
        tools=[_READ_FILE_TOOL_DEF],
        tool_handlers={"read_file": MagicMock()},
        max_iterations=3,
        max_tokens=1024,
        temperature=0.3,
        system=None,
        provider_label="openrouter",
    )
    kwargs.update(overrides)
    return openai_tool_loop(**kwargs)


# ---------------------------------------------------------------------------
# Turn-1 injection
# ---------------------------------------------------------------------------


def test_openai_tool_loop_accepts_tool_choice_first_turn_required() -> None:
    """Turn 1 forwards ``tool_choice="required"`` to ``chat.completions.create``.

    Models like qwen3-coder-next miss tools without this nudge. The knob is
    the only way the registry can signal that need to the shared loop.
    """
    # Arrange
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _openai_text_response("ok")

    # Act
    _invoke(sdk, tool_choice_first_turn="required")

    # Assert
    first_kwargs = sdk.chat.completions.create.call_args_list[0].kwargs
    assert first_kwargs.get("tool_choice") == "required"


def test_openai_tool_loop_defaults_to_auto_on_subsequent_turns() -> None:
    """Turn 2+ omits ``tool_choice`` entirely so OpenAI's implicit ``auto`` applies.

    Forcing ``required`` on every iteration would trap the model in a tool-call
    loop with no way to emit final text — defeating the whole point of the
    forced-finalize guardrail.
    """
    # Arrange
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _openai_tool_call_response(
            tool_name="read_file",
            tool_input_json='{"path": "src/x.py"}',
            tool_call_id="call_1",
        ),
        _openai_text_response('[{"file":"src/x.py","line":1}]'),
    ]
    handler = MagicMock()
    handler.return_value = MagicMock(content="ok", is_error=False)

    # Act
    _invoke(
        sdk,
        tool_handlers={"read_file": handler},
        tool_choice_first_turn="required",
    )

    # Assert — turn 1 has the kwarg; turn 2 does NOT
    calls = sdk.chat.completions.create.call_args_list
    assert calls[0].kwargs.get("tool_choice") == "required"
    assert "tool_choice" not in calls[1].kwargs


def test_openai_tool_loop_uses_auto_when_param_omitted() -> None:
    """Back-compat: callers that don't pass the new kwarg never see ``tool_choice``
    in the request kwargs, preserving today's behaviour byte-for-byte for
    OpenAI / Azure / Custom paths that haven't opted in.
    """
    # Arrange
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _openai_text_response("ok")

    # Act
    _invoke(sdk)  # no tool_choice_first_turn kwarg

    # Assert
    first_kwargs = sdk.chat.completions.create.call_args_list[0].kwargs
    assert "tool_choice" not in first_kwargs


def test_openai_tool_loop_accepts_tool_choice_first_turn_auto_without_injecting_key() -> None:
    """Passing ``"auto"`` is a no-op at the wire level — the key is simply
    absent (OpenAI's implicit default is already ``auto``). This avoids
    cluttering request payloads with redundant kwargs.
    """
    # Arrange
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _openai_text_response("ok")

    # Act
    _invoke(sdk, tool_choice_first_turn="auto")

    # Assert
    first_kwargs = sdk.chat.completions.create.call_args_list[0].kwargs
    assert "tool_choice" not in first_kwargs


def test_openai_tool_loop_accepts_tool_choice_first_turn_none() -> None:
    """``"none"`` is a legitimate value: forces the first turn to respond
    without tools, used e.g. for a synthesis-only reviewer step.
    """
    # Arrange
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = _openai_text_response("ok")

    # Act
    _invoke(sdk, tool_choice_first_turn="none")

    # Assert
    first_kwargs = sdk.chat.completions.create.call_args_list[0].kwargs
    assert first_kwargs.get("tool_choice") == "none"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_openai_tool_loop_rejects_invalid_tool_choice_first_turn() -> None:
    """Anything outside {auto, required, none} raises ``ValueError`` *before*
    any API call, so a config typo surfaces immediately rather than as a
    confusing 400 from the provider after spending tokens.
    """
    # Arrange
    sdk = MagicMock()

    # Act + Assert
    with pytest.raises(ValueError, match="tool_choice_first_turn"):
        _invoke(sdk, tool_choice_first_turn="mandatory")
    # No API call should have happened
    sdk.chat.completions.create.assert_not_called()

"""REVUE-241 Gap 2: forced-finalize guardrail for the tool-use loop.

When the loop hits ``max_iterations`` while the model is still calling tools,
the previous behaviour returned ``text_len=0`` and the parser fell through to
"0 findings" — silently dropping the agent's work. The guardrail issues one
more API call **without tools**, nudging the model to synthesise the findings
it has already identified, so a hit-the-cap run degrades to "findings from
partial info" instead of "no findings".

These tests cover both provider paths (Anthropic native + OpenAI-compatible)
and the per-agent budget plumbing.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from revue.core.tool_loop import anthropic_tool_loop, openai_tool_loop
from revue.core.tools import ToolResult


# ---------------------------------------------------------------------------
# Anthropic-path helpers
# ---------------------------------------------------------------------------

def _tool_use_block(tool_id: str, name: str, **inputs: Any) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=inputs)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _anth_resp(blocks: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _read_file_handler(path: str) -> ToolResult:
    return ToolResult(content=f"contents of {path}", is_error=False)


# ---------------------------------------------------------------------------
# Anthropic path
# ---------------------------------------------------------------------------

def test_anthropic_finalize_call_when_cap_hit_during_tool_use() -> None:
    """When max_iterations is reached with stop_reason=tool_use, the loop
    must issue ONE additional API call without tools so the model can emit
    a final structured answer instead of returning empty text."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        # Three iterations all returning tool_use → cap hit
        _anth_resp([_tool_use_block(f"tu_{i}", "read_file", path=f"f{i}.py")], "tool_use")
        for i in range(3)
    ] + [
        # Forced-finalize call returns the structured answer
        _anth_resp([_text_block('{"findings": []}')], "end_turn"),
    ]

    result = anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=3, max_tokens=1024, temperature=0.3,
        system=None, agent_name="leo",
    )

    # 3 loop iterations + 1 finalize = 4 API calls
    assert sdk.messages.create.call_count == 4

    # The finalize call must not include the tools kwarg
    final_kwargs = sdk.messages.create.call_args_list[-1][1]
    assert final_kwargs.get("tools") in (None, []), (
        f"finalize call must not pass tools; got {final_kwargs.get('tools')!r}"
    )

    # The finalize response's text must surface as the loop's result —
    # not the empty text from the cut-off pre-finalize state.
    assert result.text == '{"findings": []}'


def test_anthropic_finalize_message_uses_positive_phrasing() -> None:
    """The finalize nudge must be a positive instruction — LLMs respond
    better to 'synthesise what you have' than to 'stop calling tools'."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _anth_resp([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _anth_resp([_text_block('{"findings": []}')], "end_turn"),
    ]

    anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=1, max_tokens=1024, temperature=0.3,
        system=None, agent_name="leo",
    )

    final_messages = sdk.messages.create.call_args_list[-1][1]["messages"]
    # Flatten any text content across all messages for inspection.
    flat: list[str] = []
    for m in final_messages:
        content = m.get("content")
        if isinstance(content, str):
            flat.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    flat.append(block.get("text", ""))
    haystack = " ".join(flat).lower()

    # Positive phrasing markers — must mention the work-so-far + synthesise/emit
    assert (
        "synthes" in haystack or "emit" in haystack or "finalize" in haystack
    ), f"finalize nudge missing positive verb: {haystack[-300:]}"
    # No negative phrasing — banned words
    for banned in ("do not", "don't", "stop calling"):
        assert banned not in haystack, f"banned negative phrase {banned!r}: {haystack[-300:]}"


def test_anthropic_finalize_appends_to_existing_user_message_not_new_one() -> None:
    """Anthropic Messages API requires alternating user/assistant turns. The
    last message before finalize is the user message carrying tool_results —
    the finalize nudge must be appended to *that* message's content list, not
    added as a second adjacent user message."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _anth_resp([_tool_use_block("tu_1", "read_file", path="a.py")], "tool_use"),
        _anth_resp([_text_block('{"findings": []}')], "end_turn"),
    ]

    anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=1, max_tokens=1024, temperature=0.3,
        system=None, agent_name="leo",
    )

    final_messages = sdk.messages.create.call_args_list[-1][1]["messages"]
    # No two adjacent user messages
    for prev, curr in zip(final_messages, final_messages[1:]):
        assert not (prev["role"] == "user" and curr["role"] == "user"), (
            f"adjacent user messages would violate Anthropic API: "
            f"{prev['role']} → {curr['role']}"
        )

    # The last user message must hold both a tool_result and the finalize text
    last_user = next(m for m in reversed(final_messages) if m["role"] == "user")
    content = last_user["content"]
    assert isinstance(content, list), (
        f"last user message must be a block list to mix tool_result + finalize text; "
        f"got {type(content).__name__}"
    )
    block_types = [b.get("type") for b in content if isinstance(b, dict)]
    assert "tool_result" in block_types
    assert "text" in block_types


def test_anthropic_no_finalize_when_loop_terminates_cleanly() -> None:
    """When the model emits a final answer naturally (stop_reason=end_turn),
    no extra API call must be made — the finalize path is a guardrail, not a
    routine extra cost."""
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _anth_resp([_text_block('{"findings": []}')], "end_turn"),
    ]

    result = anthropic_tool_loop(
        sdk, model="claude-x",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, agent_name="kai",
    )

    assert sdk.messages.create.call_count == 1
    assert result.text == '{"findings": []}'


# ---------------------------------------------------------------------------
# OpenAI-compatible path
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


def _openai_tool_call(tc_id: str, name: str, args_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=args_json),
    )


def test_openai_finalize_call_when_cap_hit_during_tool_use() -> None:
    """Mirror of the Anthropic-side guardrail on the OpenAI-compatible path."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        # Three tool-use iterations → cap hit
        _openai_resp([_openai_choice(_openai_msg(
            None, tool_calls=[_openai_tool_call(f"tc_{i}", "read_file", f'{{"path":"f{i}.py"}}')]
        ))]) for i in range(3)
    ] + [
        # Forced-finalize call returns structured text
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]

    result = openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=3, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
    )

    assert sdk.chat.completions.create.call_count == 4
    final_kwargs = sdk.chat.completions.create.call_args_list[-1][1]
    assert final_kwargs.get("tools") in (None, [])
    assert result.text == '{"findings": []}'


def test_openai_finalize_omitted_when_cap_not_hit() -> None:
    """No extra API call when the model finalizes within the budget."""
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = [
        _openai_resp([_openai_choice(_openai_msg('{"findings": []}'))]),
    ]

    openai_tool_loop(
        sdk, model="gpt-4o-mini",
        messages=[{"role": "user", "content": "review"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_handlers={"read_file": _read_file_handler},
        max_iterations=5, max_tokens=1024, temperature=0.3,
        system=None, provider_label="openai",
    )

    assert sdk.chat.completions.create.call_count == 1

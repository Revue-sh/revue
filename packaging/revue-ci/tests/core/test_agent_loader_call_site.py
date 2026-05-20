"""REVUE-241: LoadedAgent._invoke_client annotates exceptions with the
client class + method that raised, so operators can tell at a glance
whether a 400/429/500 came from the tool-use path or the legacy path.

Why this matters: when Maya and Kai both fail with ``prompt is too long``,
the pipeline log currently says ``⚠ Agent maya failed: prompt is too
long (200887 to…`` with no breadcrumb to ``AnthropicClient.complete_with_tools``.
The call_site tells operators *which API call* exceeded context, which
narrows the fix from "tool-use is broken" to "read_file fan-out is over-fetching".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from revue_core.core.agent_loader import AgentDefinition, LoadedAgent


def _def(name: str = "maya") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        display_name=name.capitalize(),
        role=f"{name} role",
        system_prompt="you are a reviewer",
        max_tool_iterations=3,
    )


def _read_file_tool() -> SimpleNamespace:
    """Minimal stand-in for ReadFileTool — only needs tool_definition()/execute."""
    return SimpleNamespace(
        tool_definition=lambda: {"name": "read_file", "input_schema": {"type": "object"}},
        execute=lambda path: None,
    )


def test_invoke_client_attaches_call_site_when_complete_with_tools_raises() -> None:
    """Tool-use path: failure must point to ``complete_with_tools`` so the
    fix is correctly scoped (e.g. cap read_file fan-out, not 'retry the
    whole client')."""
    client = MagicMock()
    client.complete_with_tools.side_effect = RuntimeError("prompt is too long")
    type(client).__name__ = "AnthropicClient"

    agent = LoadedAgent(_def("maya"), client, max_tokens=4096, read_file_tool=_read_file_tool())

    with pytest.raises(RuntimeError) as excinfo:
        agent._invoke_client(user_content="diff", system_blocks=[], diff_hash="h")

    assert excinfo.value.call_site == "AnthropicClient.complete_with_tools"  # type: ignore[attr-defined]
    # Original message and chain preserved — no information loss.
    assert "prompt is too long" in str(excinfo.value)


def test_invoke_client_attaches_call_site_when_complete_raises() -> None:
    """Legacy path (no tool): failure must point to ``complete``. Tells
    operators the agent ran *without* tool-use — different debugging path."""
    client = MagicMock(spec=["complete"])  # no complete_with_tools attribute
    client.complete.side_effect = RuntimeError("upstream 500")
    type(client).__name__ = "OpenAIClient"

    agent = LoadedAgent(_def("kai"), client, max_tokens=4096, read_file_tool=None)

    with pytest.raises(RuntimeError) as excinfo:
        agent._invoke_client(user_content="diff", system_blocks=[], diff_hash="h")

    assert excinfo.value.call_site == "OpenAIClient.complete"  # type: ignore[attr-defined]


def test_invoke_client_preserves_original_exception_type() -> None:
    """Wrapping must not change the exception class — downstream code (and
    test_agent_runner.test_error_agent_captures_exception_type) reads
    ``type(exc).__name__`` to triage. Re-wrapping as RuntimeError would
    erase the original signal."""
    class _Custom400(Exception):
        pass

    client = MagicMock()
    client.complete_with_tools.side_effect = _Custom400("bad request")
    type(client).__name__ = "AnthropicClient"

    agent = LoadedAgent(_def("zara"), client, max_tokens=4096, read_file_tool=_read_file_tool())

    with pytest.raises(_Custom400) as excinfo:
        agent._invoke_client(user_content="diff", system_blocks=[], diff_hash="h")

    assert excinfo.value.call_site == "AnthropicClient.complete_with_tools"  # type: ignore[attr-defined]


def test_invoke_client_does_not_attach_call_site_on_success() -> None:
    """No exception → no annotation. Sanity check that the happy path is
    untouched by the new error-handling wrapper. REVUE-246: ``_invoke_client``
    now returns the whole CompletionResult rather than just the text — the
    classifier downstream needs stop_reason / iterations_used."""
    sentinel = SimpleNamespace(text='{"status": "clean", "summary": "ok", "confidence": 1.0}')
    client = MagicMock()
    client.complete_with_tools.return_value = sentinel
    type(client).__name__ = "AnthropicClient"

    agent = LoadedAgent(_def("leo"), client, max_tokens=4096, read_file_tool=_read_file_tool())
    out = agent._invoke_client(user_content="diff", system_blocks=[], diff_hash="h")
    assert out is sentinel

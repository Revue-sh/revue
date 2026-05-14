"""REVUE-241: LoadedAgent passes the findings schema as output_config when
calling complete_with_tools.

This is the load-bearing fix. The schema is what stops the reviewer from
emitting "Based on my analysis..." prose after a multi-turn read_file
session — grammar-constrained final text is guaranteed to match the
shape the parser expects.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from revue.core.agent_loader import AgentDefinition, LoadedAgent
from revue.core.finding_schema import THREE_STATE_SCHEMA
from revue.core.models import FileChange


def _definition(name: str = "maya") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        display_name=f"{name.title()} (Test)",
        role="test reviewer",
        system_prompt="You review code.",
        severity_default="medium",
    )


def _change(path: str = "app.py") -> FileChange:
    return FileChange(
        file_path=path,
        change_type="modified",
        additions=5, deletions=2,
        diff="@@ -1 +1 @@\n-old\n+new",
    )


_VALID_THREE_STATE_TEXT = (
    '{"status": "clean", "summary": "nothing to flag", "confidence": 0.9}'
)


def test_loaded_agent_passes_three_state_schema_when_tool_use_enabled() -> None:
    """REVUE-246: when the agent has a read_file tool wired AND the client
    supports complete_with_tools, the call must include the THREE-STATE
    schema as output_config — otherwise the model can still emit a silent
    clean or drift into prose after tool use."""
    client = MagicMock()
    client.complete_with_tools.return_value = SimpleNamespace(
        text=_VALID_THREE_STATE_TEXT, usage=SimpleNamespace(),
    )

    read_tool = MagicMock()
    read_tool.tool_definition.return_value = {"name": "read_file", "input_schema": {}}

    agent = LoadedAgent(
        _definition(), client, max_tokens=4096, read_file_tool=read_tool,
    )
    agent.analyse([_change()])

    assert client.complete_with_tools.called, (
        "complete_with_tools must be called when a read_file_tool is present"
    )
    kwargs = client.complete_with_tools.call_args[1]
    output_config = kwargs.get("output_config")
    assert output_config is not None, (
        "LoadedAgent must pass output_config when invoking with tools — "
        "without it the grammar never constrains the final response"
    )
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["schema"] == THREE_STATE_SCHEMA


def test_loaded_agent_omits_output_config_when_no_tool() -> None:
    """When no read_file tool is present, the agent falls back to
    complete() — and complete() does not take output_config. The fallback
    path must not regress; structured outputs ride only on the tool path."""
    client = MagicMock()
    client.complete.return_value = SimpleNamespace(
        text=_VALID_THREE_STATE_TEXT,
        usage=SimpleNamespace(),
    )

    agent = LoadedAgent(_definition(), client, max_tokens=4096, read_file_tool=None)
    agent.analyse([_change()])

    assert client.complete.called
    assert not client.complete_with_tools.called
    # Whatever complete() got, output_config should not be among the kwargs —
    # the older complete() signature predates structured outputs and we don't
    # extend it in this change.
    assert "output_config" not in client.complete.call_args[1]


@pytest.mark.parametrize("agent_name", ["maya", "leo", "kai", "zara"])
def test_output_config_threaded_for_each_reviewer(agent_name: str) -> None:
    """The four reviewer agents each get the three-state schema — no
    agent-specific branch should drop it on the way through. Pinned per-agent
    so a future refactor that special-cases one agent can't silently break
    the others."""
    client = MagicMock()
    client.complete_with_tools.return_value = SimpleNamespace(
        text=_VALID_THREE_STATE_TEXT, usage=SimpleNamespace(),
    )

    read_tool = MagicMock()
    read_tool.tool_definition.return_value = {"name": "read_file", "input_schema": {}}

    LoadedAgent(
        _definition(agent_name), client, max_tokens=4096, read_file_tool=read_tool,
    ).analyse([_change()])

    assert (
        client.complete_with_tools.call_args[1].get("output_config", {})
        .get("format", {}).get("schema") == THREE_STATE_SCHEMA
    )

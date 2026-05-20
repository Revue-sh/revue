"""REVUE-241 Gap 2: per-agent ``max_tool_iterations`` budget.

Different reviewers have different scopes — Leo's architecture review of a
92-file diff legitimately needs more `read_file` calls than Kai's narrow
performance pass. Hardcoding a single ceiling in ``agent_loader.py`` violates
OCP (adding a new agent shouldn't require editing the loader) and forces a
one-size-fits-all magic number.

These tests pin the contract:
  * ``AgentDefinition`` carries a ``max_tool_iterations`` field
  * The dict parser reads it from YAML/MD front-matter
  * ``LoadedAgent`` threads the per-agent value into ``complete_with_tools``
  * Default is 5 for back-compat with the historical loader value
"""
from __future__ import annotations

from unittest.mock import MagicMock

from revue_core.core.agent_loader import AgentDefinition, LoadedAgent, _dict_to_definition
from revue_core.core.ai_client import CompletionResult, TokenUsage
from revue_core.core.models import FileChange


# ---------------------------------------------------------------------------
# AgentDefinition.max_tool_iterations
# ---------------------------------------------------------------------------

def test_agent_definition_defaults_max_tool_iterations_to_five() -> None:
    """Back-compat: agents without an explicit budget keep the historical 5."""
    defn = AgentDefinition(
        name="kai",
        display_name="Kai",
        role="perf",
        system_prompt="...",
    )
    assert defn.max_tool_iterations == 5


def test_agent_definition_accepts_explicit_max_tool_iterations() -> None:
    """Per-agent override stored on the definition."""
    defn = AgentDefinition(
        name="leo",
        display_name="Leo",
        role="architecture",
        system_prompt="...",
        max_tool_iterations=12,
    )
    assert defn.max_tool_iterations == 12


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_dict_to_definition_reads_max_tool_iterations_from_data() -> None:
    """The shared parser used by both YAML and MD front-matter must pick up
    the field — otherwise per-agent config has no surface in the data files."""
    defn = _dict_to_definition({
        "name": "leo",
        "display_name": "Leo",
        "role": "architecture",
        "system_prompt": "...",
        "max_tool_iterations": 12,
    })
    assert defn.max_tool_iterations == 12


def test_dict_to_definition_defaults_when_field_absent() -> None:
    """Missing field → default 5, preserving compatibility with every
    existing agent definition in src/revue/agents/."""
    defn = _dict_to_definition({
        "name": "kai",
        "display_name": "Kai",
        "role": "perf",
        "system_prompt": "...",
    })
    assert defn.max_tool_iterations == 5


# ---------------------------------------------------------------------------
# LoadedAgent → complete_with_tools wiring
# ---------------------------------------------------------------------------

def _make_client_capturing_call_kwargs() -> MagicMock:
    client = MagicMock()
    client.complete_with_tools = MagicMock(return_value=CompletionResult(
        text='{"findings": []}', usage=TokenUsage(),
    ))
    return client


def _fc(path: str) -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=1, deletions=0, diff=f"diff for {path}",
    )


def test_loaded_agent_passes_definition_max_tool_iterations_to_complete_with_tools() -> None:
    """The per-agent budget must flow from the definition into the API call —
    the whole point of the field is that the loader uses it instead of a
    hardcoded constant."""
    from revue_core.core.tools.read_file import ReadFileTool
    from pathlib import Path

    defn = AgentDefinition(
        name="leo", display_name="Leo", role="architecture", system_prompt="...",
        max_tool_iterations=12,
    )
    client = _make_client_capturing_call_kwargs()
    tool = ReadFileTool(Path.cwd(), {"test.py"})
    agent = LoadedAgent(defn, client, 4096, read_file_tool=tool)
    agent.analyse([_fc("test.py")])

    client.complete_with_tools.assert_called_once()
    kwargs = client.complete_with_tools.call_args[1]
    assert kwargs["max_iterations"] == 12, (
        f"expected per-agent budget 12, got {kwargs['max_iterations']}"
    )


def test_loaded_agent_passes_default_five_when_definition_has_no_override() -> None:
    """No explicit per-agent budget → loader passes the historical default 5,
    matching pre-Gap-2 behaviour for every untouched agent."""
    from revue_core.core.tools.read_file import ReadFileTool
    from pathlib import Path

    defn = AgentDefinition(
        name="kai", display_name="Kai", role="perf", system_prompt="...",
    )  # no max_tool_iterations specified
    client = _make_client_capturing_call_kwargs()
    tool = ReadFileTool(Path.cwd(), {"test.py"})
    agent = LoadedAgent(defn, client, 4096, read_file_tool=tool)
    agent.analyse([_fc("test.py")])

    kwargs = client.complete_with_tools.call_args[1]
    assert kwargs["max_iterations"] == 5

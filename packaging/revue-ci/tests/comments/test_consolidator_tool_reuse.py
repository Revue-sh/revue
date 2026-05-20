"""Regression: NovaSingleShotStrategy reuses one ReadFileTool across groups.

Previously the tool, the schema dict, and the handler map were rebuilt on
every synthesise() call. After parallelising Pass B in REVUE-240, this
became 8 workers × ~30 groups = ~240 allocations per run. Cosmetic in
absolute terms, but reviewers flagged it four separate times across the
last dogfood — pin the contract.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from revue_core.comments.consolidator import NovaSingleShotStrategy
from revue_core.comments.models import AgentFinding, Attribution, SynthesisGroup
from revue_core.core.tools import ReadFileTool


def _finding(file_path: str = "f.py", line: int = 10) -> AgentFinding:
    return AgentFinding(
        file_path=file_path,
        line_number=line,
        severity="medium",
        issue="x",
        suggestion="y",
        confidence=0.8,
        category="code-quality",
        agent_name="maya",
        code_replacement=None,
        replacement_line_count=1,
        snippet="",
    )


def _group(file_path: str = "f.py", line: int = 10) -> SynthesisGroup:
    return SynthesisGroup(
        findings=[_finding(file_path=file_path, line=line)],
        file_path=file_path,
        line_range=(line, line),
        group_type="singleton",
    )


def test_read_file_tool_created_once_in_init(tmp_path: Path) -> None:
    """The tool must be built in __init__, not on every synthesise() call."""
    client = MagicMock()
    client.complete_with_tools.return_value = MagicMock(text='[{"file":"f.py","line":10,"issue":"x","suggestion":"y","severity":"medium"}]')

    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"f.py": "@@ -1 +1 @@\n-old\n+new"},
        repo_root=tmp_path,
    )

    # Tool, schema list, and handler map exist after construction.
    assert isinstance(strategy._read_file_tool, ReadFileTool)
    assert strategy._tools == [ReadFileTool.tool_definition()]
    assert "read_file" in strategy._tool_handlers

    # Identity is preserved across multiple synthesise() calls.
    tool_before = strategy._read_file_tool
    handlers_before = strategy._tool_handlers
    tools_before = strategy._tools

    for _ in range(3):
        strategy.synthesise(_group())

    assert strategy._read_file_tool is tool_before, (
        "ReadFileTool must be reused across synthesise() calls"
    )
    assert strategy._tool_handlers is handlers_before
    assert strategy._tools is tools_before


def test_complete_with_tools_receives_the_init_built_tools(tmp_path: Path) -> None:
    """Each Nova call must hand the SAME tool/handler objects to the client."""
    client = MagicMock()
    client.complete_with_tools.return_value = MagicMock(text='[{"file":"f.py","line":10,"issue":"x","suggestion":"y","severity":"medium"}]')

    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"f.py": "diff"},
        repo_root=tmp_path,
    )

    strategy.synthesise(_group())
    strategy.synthesise(_group())

    calls = client.complete_with_tools.call_args_list
    assert len(calls) == 2
    # Both calls must reference the same tool list and handler map objects.
    assert calls[0].kwargs["tools"] is calls[1].kwargs["tools"]
    assert calls[0].kwargs["tool_handlers"] is calls[1].kwargs["tool_handlers"]

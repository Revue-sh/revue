"""Tests for reviewer_tools.py — ReadFileTool factory (REVUE-241)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from revue_core.core.ai_config import AIConfig
from revue_core.core.models import FileChange
from revue_core.core.reviewer_tools import build_reviewer_read_file_tool
from revue_core.core.tools.read_file import ReadFileTool


def _config(reviewer_tool_use: bool = True) -> AIConfig:
    return AIConfig(
        gitlab_url="", gitlab_token="", gitlab_project_id="",
        gitlab_project_path="", gitlab_project_url="",
        genai_gateway_url="", openai_api_key="", gen_ai_gateway_model="",
        ai_temp=0.3, ai_confidence=70, ai_max_tokens=4096,
        reviewer_tool_use=reviewer_tool_use,
    )


def _fc(path: str = "app.py") -> FileChange:
    return FileChange(
        file_path=path,
        change_type="modified",
        additions=5,
        deletions=2,
        diff="@@ -1 +1 @@\n-old\n+new",
    )


def test_returns_none_when_flag_disabled():
    """reviewer_tool_use=False → builder returns None (no tool wired)."""
    config = _config(reviewer_tool_use=False)
    tool = build_reviewer_read_file_tool(config, [_fc()])
    assert tool is None


def test_returns_tool_when_flag_enabled():
    """reviewer_tool_use=True (default) → builder returns a ReadFileTool."""
    config = _config(reviewer_tool_use=True)
    tool = build_reviewer_read_file_tool(config, [_fc()])
    assert isinstance(tool, ReadFileTool)


def test_allowed_paths_match_diff_files():
    """ReadFileTool.allowed_paths is the set of file_path from the FileChange list."""
    config = _config()
    changes = [_fc("src/a.py"), _fc("tests/b.py"), _fc("README.md")]
    tool = build_reviewer_read_file_tool(config, changes)
    assert tool is not None
    assert tool._allowed_paths == {"src/a.py", "tests/b.py", "README.md"}


def test_uses_explicit_repo_root_when_provided(tmp_path):
    """Explicit repo_root argument overrides Path.cwd()."""
    config = _config()
    tool = build_reviewer_read_file_tool(config, [_fc()], repo_root=tmp_path)
    assert tool is not None
    assert tool._repo_root == tmp_path.resolve()


def test_defaults_repo_root_to_cwd():
    """Without explicit repo_root, builder uses Path.cwd()."""
    config = _config()
    tool = build_reviewer_read_file_tool(config, [_fc()])
    assert tool is not None
    assert tool._repo_root == Path.cwd().resolve()


def test_builder_defaults_to_enabled_when_config_lacks_flag():
    """Configs without a reviewer_tool_use attribute (legacy / stub configs)
    must default to enabled — the builder uses getattr(..., default=True).

    This pins the back-compat path: a SimpleNamespace stand-in for AIConfig
    works without explicitly setting the new field.
    """
    from types import SimpleNamespace
    bare_config = SimpleNamespace()  # no reviewer_tool_use attribute at all
    tool = build_reviewer_read_file_tool(bare_config, [_fc()])
    assert isinstance(tool, ReadFileTool), (
        "missing flag must default to True so existing configs keep working"
    )


def test_empty_changes_still_returns_tool():
    """Empty file list → tool still built with empty allowed_paths set."""
    config = _config()
    tool = build_reviewer_read_file_tool(config, [])
    assert tool is not None
    assert tool._allowed_paths == set()


def test_initialisation_failure_returns_none():
    """If ReadFileTool init raises, builder returns None (no exception propagated)."""
    config = _config()

    with patch(
        "revue_core.core.reviewer_tools.ReadFileTool",
        side_effect=OSError("simulated failure"),
    ):
        tool = build_reviewer_read_file_tool(config, [_fc()])

    assert tool is None


def test_initialisation_typeerror_does_not_crash_pipeline():
    """The docstring promises 'keeps the pipeline's happy path intact' on any
    init failure. A TypeError from ReadFileTool (e.g. signature drift in a
    future refactor) must be caught, not propagated."""
    config = _config()

    with patch(
        "revue_core.core.reviewer_tools.ReadFileTool",
        side_effect=TypeError("signature mismatch"),
    ):
        tool = build_reviewer_read_file_tool(config, [_fc()])

    assert tool is None


def test_initialisation_runtimeerror_does_not_crash_pipeline():
    """Any unexpected Exception subclass must degrade to no-tool, not crash."""
    config = _config()

    with patch(
        "revue_core.core.reviewer_tools.ReadFileTool",
        side_effect=RuntimeError("unexpected"),
    ):
        tool = build_reviewer_read_file_tool(config, [_fc()])

    assert tool is None


def test_initialisation_keyboardinterrupt_still_propagates():
    """BaseException-derived signals (KeyboardInterrupt) MUST propagate so
    Ctrl-C still aborts the pipeline. The broadened catch is `Exception`,
    not bare `except:`."""
    config = _config()

    with patch(
        "revue_core.core.reviewer_tools.ReadFileTool",
        side_effect=KeyboardInterrupt(),
    ):
        with pytest.raises(KeyboardInterrupt):
            build_reviewer_read_file_tool(config, [_fc()])


def test_explicit_repo_root_takes_precedence_over_cwd(tmp_path, monkeypatch):
    """When pipeline passes an explicit repo_root, the builder uses it even
    if Path.cwd() resolves elsewhere. REVUE-241 P6: this guarantees that
    reviewers and Nova/Vex sandbox to the same root.
    """
    # Move cwd somewhere unrelated to verify the explicit arg wins.
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)

    explicit_root = tmp_path / "the_real_repo"
    explicit_root.mkdir()

    tool = build_reviewer_read_file_tool(_config(), [_fc()], repo_root=explicit_root)
    assert tool is not None
    assert tool._repo_root == explicit_root.resolve()
    assert tool._repo_root != Path.cwd().resolve()

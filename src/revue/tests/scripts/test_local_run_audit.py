"""Slice 2 — reviewer-tools prompt constraint + soft post-hoc audit.

Validates that ``scripts/local_run.py``:

1. Injects an explicit tool-scope constraint plus the diff file list into the
   Phase 1 user prompt (``cmd_prepare``), so Agent forks know which paths they
   are allowed to read.
2. In Phase 3 ``cmd_consolidate``, cross-checks every finding's ``file_path``
   against the keys of ``diff_by_file.json``. Out-of-diff references emit a
   single stderr warning per agent — purely observability; findings are
   never rejected, dropped, or rewritten.

The helpers under test live in ``scripts/local_run.py`` (not a Python package);
they are loaded by absolute path, same pattern as
``test_local_run_three_state.py``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]
_LOCAL_RUN_PATH = _REPO_ROOT / "scripts" / "local_run.py"


def _load_local_run():
    """Load scripts/local_run.py as a module by absolute path."""
    src_path = str(_REPO_ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    spec = importlib.util.spec_from_file_location("revue_local_run", _LOCAL_RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def local_run():
    return _load_local_run()


# ---------------------------------------------------------------------------
# Phase 3 audit — out-of-diff path detection (observability only)
# ---------------------------------------------------------------------------


def test_local_run_audit_warns_on_outside_path(local_run, capsys):
    """A finding whose file_path is outside the diff yields a stderr warning.

    The warning names the offending agent and lists each out-of-diff path.
    Findings themselves are returned unchanged — audit is observability,
    not enforcement.
    """
    diff_files = {"src/revue/cli.py", "scripts/local_run.py"}
    findings = [
        {"file_path": "/etc/passwd", "line_number": 1, "issue": "x"},
        {"file_path": "src/revue/cli.py", "line_number": 10, "issue": "ok"},
    ]

    out_of_diff = local_run._audit_finding_paths(
        agent_name="maya",
        findings=findings,
        diff_files=diff_files,
    )

    assert out_of_diff == ["/etc/passwd"]
    captured = capsys.readouterr()
    assert "maya" in captured.err
    assert "/etc/passwd" in captured.err
    # In-diff path must NOT appear in the warning line itself.
    warning_lines = [l for l in captured.err.splitlines() if "out-of-diff" in l or "tool-audit" in l]
    assert warning_lines, "expected an audit warning line on stderr"
    for line in warning_lines:
        assert "src/revue/cli.py" not in line


def test_local_run_audit_silent_on_in_diff_paths(local_run, capsys):
    """All findings referencing in-diff paths produces zero stderr output."""
    diff_files = {"src/revue/cli.py", "scripts/local_run.py"}
    findings = [
        {"file_path": "src/revue/cli.py", "line_number": 10, "issue": "a"},
        {"file_path": "scripts/local_run.py", "line_number": 20, "issue": "b"},
    ]

    out_of_diff = local_run._audit_finding_paths(
        agent_name="zara",
        findings=findings,
        diff_files=diff_files,
    )

    assert out_of_diff == []
    captured = capsys.readouterr()
    assert captured.err == ""


def test_local_run_audit_handles_missing_file_path_field(local_run, capsys):
    """Findings without a ``file_path`` field are skipped (no crash, no false warning)."""
    diff_files = {"src/revue/cli.py"}
    findings = [
        {"line_number": 1, "issue": "no path"},  # malformed — no file_path
        {"file_path": "src/revue/cli.py", "line_number": 2, "issue": "ok"},
    ]

    out_of_diff = local_run._audit_finding_paths(
        agent_name="kai",
        findings=findings,
        diff_files=diff_files,
    )

    assert out_of_diff == []
    captured = capsys.readouterr()
    assert captured.err == ""


def test_local_run_audit_emits_single_warning_per_agent(local_run, capsys):
    """Two out-of-diff paths from one agent collapse into one warning line."""
    diff_files = {"src/revue/cli.py"}
    findings = [
        {"file_path": "/etc/passwd", "line_number": 1, "issue": "x"},
        {"file_path": "../secret.env", "line_number": 2, "issue": "y"},
    ]

    out_of_diff = local_run._audit_finding_paths(
        agent_name="leo",
        findings=findings,
        diff_files=diff_files,
    )

    assert out_of_diff == ["/etc/passwd", "../secret.env"]
    captured = capsys.readouterr()
    # Exactly one warning line for this agent — both offending paths included.
    warning_lines = [l for l in captured.err.splitlines() if l.strip()]
    assert len(warning_lines) == 1
    assert "/etc/passwd" in warning_lines[0]
    assert "../secret.env" in warning_lines[0]
    assert "leo" in warning_lines[0]


# ---------------------------------------------------------------------------
# Phase 1 prompt — tool constraint + diff file list injection
# ---------------------------------------------------------------------------


def test_local_run_prompt_contains_file_list(local_run):
    """The user prompt builder embeds the diff file paths and the tool-scope
    constraint sentence.

    Verifies AC1: cmd_prepare's prompt instructs the Agent fork that reads
    are restricted to the listed paths.
    """
    diff_files = ["src/revue/cli.py", "scripts/local_run.py", "src/revue/agents/maya.md"]

    prompt = local_run._build_tool_scope_constraint(diff_files)

    # Constraint sentence — verbatim phrase from the plan.
    assert "Available tools: Read, Grep, Bash" in prompt
    assert "Restrict reads to the file paths listed below" in prompt
    assert "Reading paths outside this list invalidates your findings" in prompt

    # File list — every path appears.
    for path in diff_files:
        assert path in prompt


def test_local_run_prompt_constraint_empty_file_list(local_run):
    """Empty file list still yields a coherent prompt (no crash, constraint
    sentence present, list section header present)."""
    prompt = local_run._build_tool_scope_constraint([])
    assert "Available tools: Read, Grep, Bash" in prompt

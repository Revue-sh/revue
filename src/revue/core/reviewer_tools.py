"""Reviewer-tool factory — builds ReadFileTool for reviewer agents (REVUE-241).

SRP: this module owns the *decision* and *construction* of which tool-use
infrastructure the reviewer agents (Maya/Leo/Kai/Zara) receive on a given
pipeline run. The pipeline only asks for a tool; it does not know what
config flags, sandbox rules, or error policies apply.

OCP: adding a new reviewer tool (e.g. ``grep``, ``read_directory``) means
adding a new builder here — the pipeline call site stays unchanged.

DIP: ``ReviewPipeline`` depends on this builder, not on ``ReadFileTool``
directly. Tests can monkey-patch this module to inject fakes without
touching pipeline.py.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_config import AIConfig
    from .models import FileChange

from dataclasses import dataclass

from .tools.find_code import FindCodeTool
from .tools.read_file import ReadFileTool
from .tools.read_lines import ReadLinesTool

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewerToolset:
    """Bundle of tools handed to reviewer agents. Any field can be ``None``
    when the corresponding tool is disabled or failed to initialise."""

    read_file: "ReadFileTool | None"
    read_lines: "ReadLinesTool | None"
    find_code: "FindCodeTool | None"


def build_reviewer_read_file_tool(
    config: "AIConfig",
    changes: "list[FileChange]",
    repo_root: "Path | None" = None,
) -> "ReadFileTool | None":
    """Build a ReadFileTool for reviewer agents — or ``None`` when disabled.

    Returns ``None`` (no tool) when:
      * ``config.reviewer_tool_use`` is False, or
      * tool initialisation raises (e.g. missing repo root).

    Returning ``None`` rather than raising keeps the pipeline's happy path
    intact — reviewers fall back to diff-only review without surfacing a
    hard error to the user.
    """
    if not getattr(config, "reviewer_tool_use", True):
        _log.debug("[revue]   reviewer_tool_use disabled — skipping tool wiring")
        return None

    try:
        allowed_paths = {fc.file_path for fc in changes}
        tool = ReadFileTool(
            repo_root=repo_root or Path.cwd(),
            allowed_paths=allowed_paths,
        )
        _log.debug("[revue]   ReadFileTool initialized for %d file(s)", len(allowed_paths))
        return tool
    except Exception as exc:
        # Catch Exception (not BaseException) so Ctrl-C / SystemExit still
        # abort the pipeline. The docstring promises that ANY init failure
        # degrades to no-tool rather than crashing the review.
        _log.warning(
            "[revue]   ReadFileTool initialisation failed (%s: %s) — reviewer tool-use disabled",
            type(exc).__name__, exc,
        )
        return None


def build_reviewer_toolset(
    config: "AIConfig",
    changes: "list[FileChange]",
    repo_root: "Path | None" = None,
) -> "ReviewerToolset":
    """REVUE-243: build the full reviewer toolset (read_file + read_lines +
    find_code). Single ``reviewer_tool_use`` config flag still gates the
    whole set — turning it off leaves all three as None and the loop
    silently degrades to diff-only review.

    Each tool's construction is independently try/except'd so a failure in
    one (e.g. FindCodeTool subprocess setup on an exotic platform) doesn't
    deprive reviewers of the others."""
    if not getattr(config, "reviewer_tool_use", True):
        _log.debug("[revue]   reviewer_tool_use disabled — skipping tool wiring")
        return ReviewerToolset(read_file=None, read_lines=None, find_code=None)

    repo = repo_root or Path.cwd()
    allowed_paths = {fc.file_path for fc in changes}

    def _safe_build(name: str, factory):
        try:
            return factory()
        except Exception as exc:
            _log.warning(
                "[revue]   %s init failed (%s: %s) — tool disabled for this run",
                name, type(exc).__name__, exc,
            )
            return None

    read_file = _safe_build(
        "ReadFileTool",
        lambda: ReadFileTool(repo_root=repo, allowed_paths=allowed_paths),
    )
    read_lines = _safe_build(
        "ReadLinesTool",
        lambda: ReadLinesTool(repo_root=repo, allowed_paths=allowed_paths),
    )
    find_code = _safe_build(
        "FindCodeTool",
        lambda: FindCodeTool(repo_root=repo, allowed_paths=allowed_paths),
    )

    _log.debug(
        "[revue]   ReviewerToolset built for %d file(s): read_file=%s read_lines=%s find_code=%s",
        len(allowed_paths),
        read_file is not None, read_lines is not None, find_code is not None,
    )
    return ReviewerToolset(read_file=read_file, read_lines=read_lines, find_code=find_code)

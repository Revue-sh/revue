"""Tests for NovaSingleShotStrategy tool-use wiring (REVUE-239).

Two architectural changes together:

  1. All groups (including singletons) route through Nova. The historical
     ``_passthrough`` short-circuit for 1-finding groups is gone — Nova now
     synthesises every finding so it can produce coherent span anchors using
     file context.
  2. Nova is given the ``read_file`` tool via ``complete_with_tools``. The
     tool is sandboxed to the PR's touched files (``diff_by_file`` keys).

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from revue.comments.consolidator import NovaSingleShotStrategy
from revue.comments.models import AgentFinding, SynthesisGroup
from revue.core.ai_client import CompletionResult, TokenUsage
from revue.core.tools import ReadFileTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    *,
    file_path: str = "src/example.py",
    line_number: int = 42,
    severity: str = "medium",
    issue: str = "missing input validation",
    suggestion: str = "raise ValueError when input is None",
    confidence: float = 0.85,
    category: str = "code-quality",
    agent_name: str = "maya",
    code_replacement: "list[str] | None" = None,
    replacement_line_count: int = 1,
) -> AgentFinding:
    return AgentFinding(
        file_path=file_path,
        line_number=line_number,
        severity=severity,
        issue=issue,
        suggestion=suggestion,
        confidence=confidence,
        category=category,
        agent_name=agent_name,
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
    )


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, usage=TokenUsage())


def _nova_json_one(*, file_path: str, line: int, issue: str = "synthesised") -> str:
    """Build a Nova response string with a single coherent finding."""
    import json
    return json.dumps([{
        "file": file_path,
        "line": line,
        "issue": issue,
        "suggestion": "do the thing",
        "severity": "medium",
        "code_replacement": None,
    }])


# ---------------------------------------------------------------------------
# Singleton routing — no more passthrough
# ---------------------------------------------------------------------------


def test_singleton_group_routes_through_nova_via_complete_with_tools(tmp_path: Path) -> None:
    """A singleton group must trigger complete_with_tools on the client — not a passthrough."""
    # Arrange
    client = MagicMock()
    client.complete_with_tools.return_value = _completion(
        _nova_json_one(file_path="src/example.py", line=42)
    )
    # complete() must NOT be called — confirms passthrough is gone
    client.complete.side_effect = AssertionError("complete() should not be invoked when tool-use is wired")

    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/example.py": "@@ -0,0 +42,1 @@\n+x = 1\n"},
        repo_root=tmp_path,
    )
    group = SynthesisGroup(
        findings=[_finding()],
        file_path="src/example.py",
        line_range=(42, 42),
        group_type="singleton",
    )

    # Act
    result = strategy.synthesise(group)

    # Assert
    client.complete_with_tools.assert_called_once()
    assert result.file_path == "src/example.py"
    assert result.line_number == 42


# ---------------------------------------------------------------------------
# Tool wiring — read_file is offered with the PR's diff_by_file as sandbox
# ---------------------------------------------------------------------------


def test_synthesise_offers_read_file_tool_definition_to_nova(tmp_path: Path) -> None:
    """The tools list passed to complete_with_tools must include the read_file schema."""
    # Arrange
    client = MagicMock()
    client.complete_with_tools.return_value = _completion(
        _nova_json_one(file_path="src/a.py", line=10)
    )
    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/a.py": "@@ -0,0 +10,1 @@\n+pass\n"},
        repo_root=tmp_path,
    )
    group = SynthesisGroup(
        findings=[_finding(file_path="src/a.py", line_number=10)],
        file_path="src/a.py",
        line_range=(10, 10),
        group_type="singleton",
    )

    # Act
    strategy.synthesise(group)

    # Assert
    call_kwargs = client.complete_with_tools.call_args.kwargs
    tools = call_kwargs["tools"]
    tool_names = [t["name"] for t in tools]
    assert "read_file" in tool_names


def test_synthesise_handler_is_scoped_to_pr_files_only(tmp_path: Path) -> None:
    """The handler dict must bind read_file to a ReadFileTool sandboxed to diff_by_file keys.

    Verified by invoking the registered handler with a path NOT in diff_by_file —
    the result must be is_error=True with a PR-files message.
    """
    # Arrange
    client = MagicMock()
    client.complete_with_tools.return_value = _completion(
        _nova_json_one(file_path="src/in_pr.py", line=5)
    )
    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/in_pr.py": "@@ -0,0 +5,1 @@\n+y = 2\n"},
        repo_root=tmp_path,
    )
    group = SynthesisGroup(
        findings=[_finding(file_path="src/in_pr.py", line_number=5)],
        file_path="src/in_pr.py",
        line_range=(5, 5),
        group_type="singleton",
    )

    # Act
    strategy.synthesise(group)
    handler = client.complete_with_tools.call_args.kwargs["tool_handlers"]["read_file"]
    blocked = handler(path="src/not_in_pr.py")

    # Assert — sandbox rejects path outside diff_by_file
    assert blocked.is_error is True
    assert "PR" in blocked.content


def test_synthesise_handler_reads_pr_file_at_head(tmp_path: Path) -> None:
    """The handler reads a real PR file: round-trip Nova's handler dict against the FS."""
    # Arrange
    target = tmp_path / "src" / "in_pr.py"
    target.parent.mkdir(parents=True)
    target.write_text("def shipped():\n    return True\n")

    client = MagicMock()
    client.complete_with_tools.return_value = _completion(
        _nova_json_one(file_path="src/in_pr.py", line=1)
    )
    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/in_pr.py": "@@ -0,0 +1,2 @@\n+def shipped():\n+    return True\n"},
        repo_root=tmp_path,
    )
    group = SynthesisGroup(
        findings=[_finding(file_path="src/in_pr.py", line_number=1)],
        file_path="src/in_pr.py",
        line_range=(1, 1),
        group_type="singleton",
    )

    # Act
    strategy.synthesise(group)
    handler = client.complete_with_tools.call_args.kwargs["tool_handlers"]["read_file"]
    result = handler(path="src/in_pr.py")

    # Assert — handler returned the verbatim file content
    assert result.is_error is False
    assert result.content == "def shipped():\n    return True\n"


# ---------------------------------------------------------------------------
# Fallback — Nova failure (exception) still produces a result
# ---------------------------------------------------------------------------


def test_synthesise_falls_back_to_deterministic_when_nova_raises(tmp_path: Path) -> None:
    """Network/API failure during complete_with_tools: synthesise still returns a ConsolidatedFinding."""
    # Arrange
    client = MagicMock()
    client.complete_with_tools.side_effect = RuntimeError("API timeout")
    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/example.py": "@@ -0,0 +42,1 @@\n+x = 1\n"},
        repo_root=tmp_path,
    )
    group = SynthesisGroup(
        findings=[_finding()],
        file_path="src/example.py",
        line_range=(42, 42),
        group_type="singleton",
    )

    # Act
    result = strategy.synthesise(group)

    # Assert — deterministic_fallback returned a valid ConsolidatedFinding (no raise)
    assert result is not None
    assert result.file_path == "src/example.py"
    assert result.line_number == 42


# ---------------------------------------------------------------------------
# Reproducer — Finding 1 misanchor on PR #24 (REVUE-239 Phase 1)
# ---------------------------------------------------------------------------

# Real-world scenario observed in GitHub Actions run 25692818944:
# Three agents (Maya, Zara, Kai) flagged a security concern at line 86 of a new
# file, but the actual code they wanted to rewrite was at line 91. Their
# code_replacement was at indent 8; line 86 in the diff was inside a content=
# f-string at indent 16. Nova synthesised the group but the consolidator
# overrode her chosen `line` with group.line_range[0] (86), producing a
# destructive multi-line suggestion anchored at the wrong location.
#
# After Phase 1: Nova's `line` and `replacement_line_count` are authoritative.
# Validators removed; semantic correctness is Vex's job (REVUE-240).

_READ_FILE_DIFF = """\
@@ -0,0 +1,145 @@
+\"\"\"Sandboxed file-read tool.\"\"\"
+from pathlib import Path
+
+
+class ReadFileTool:
+    def __init__(self, repo_root, allowed_paths):
+        self._repo_root = repo_root.resolve()
+        self._allowed_paths = allowed_paths
+
+    def execute(self, *, path):
+        if path not in self._allowed_paths:
+            return ToolResult(
+                content=(
+                    f\"Error: '{path}' is not in this PR's file set. \"
+                    f\"read_file can only access files touched by the PR. \"
+                    f\"If you need wider context, omit code_replacement and \"
+                    f\"explain the limitation in your prose suggestion.\"
+                ),
+                is_error=True,
+            )
+
+        full_path = (self._repo_root / path).resolve()
+        try:
+            full_path.relative_to(self._repo_root)
+        except ValueError:
+            return ToolResult(
+                content=(
+                    f\"Error: '{path}' resolves outside the repo root.\"
+                ),
+                is_error=True,
+            )
+
+        return ToolResult(content=full_path.read_text(), is_error=False)
"""


def _security_finding(*, agent: str, line: int, confidence: float) -> AgentFinding:
    """Build a security finding shaped like the PR #24 ones — code_replacement at indent 8."""
    return AgentFinding(
        file_path="src/revue/core/tools/read_file.py",
        line_number=line,
        severity="medium",
        issue="symlink-based path traversal not prevented",
        suggestion="use strict=True on resolve() and relative_to()",
        confidence=confidence,
        category="security",
        agent_name=agent,
        code_replacement=[
            "        full_path = (self._repo_root / path).resolve(strict=True)",
            "        try:",
            "            full_path.relative_to(self._repo_root.resolve(strict=True))",
            "        except ValueError:",
        ],
        replacement_line_count=4,
    )


def test_nova_chosen_line_overrides_group_first_line_when_present(tmp_path: Path) -> None:
    """Reproducer for PR #24 Finding 1: Nova's `line` must win over group.line_range[0]."""
    # Arrange — three agents all reported line 86 (the wrong anchor — inside an f-string)
    findings = [
        _security_finding(agent="maya", line=86, confidence=0.85),
        _security_finding(agent="zara", line=86, confidence=0.80),
        _security_finding(agent="kai",  line=86, confidence=0.75),
    ]
    group = SynthesisGroup(
        findings=findings,
        file_path="src/revue/core/tools/read_file.py",
        line_range=(86, 86),
        group_type="same_line",
    )

    # Nova reads the file and corrects the anchor to line 91 (where resolve() actually lives)
    import json
    nova_json = json.dumps([{
        "file": "src/revue/core/tools/read_file.py",
        "line": 91,
        "replacement_line_count": 4,
        "issue": "symlink traversal vulnerability",
        "suggestion": "use strict=True on resolve() and relative_to()",
        "severity": "medium",
        "code_replacement": [
            "        full_path = (self._repo_root / path).resolve(strict=True)",
            "        try:",
            "            full_path.relative_to(self._repo_root.resolve(strict=True))",
            "        except ValueError:",
        ],
    }])

    client = MagicMock()
    client.complete_with_tools.return_value = _completion(nova_json)
    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/revue/core/tools/read_file.py": _READ_FILE_DIFF},
        repo_root=tmp_path,
    )

    # Act
    result = strategy.synthesise(group)

    # Assert — Nova's anchor (91), not the group's (86), is on the ConsolidatedFinding
    assert result.line_number == 91, (
        f"Expected Nova's chosen line (91) to win; got {result.line_number}. "
        f"Consolidator is still overriding Nova's anchor with group.line_range[0]."
    )
    assert result.replacement_line_count == 4
    # code_replacement preserved verbatim — no deterministic validator drops it
    assert result.code_replacement is not None
    assert len(result.code_replacement) == 4
    assert result.code_replacement[0].lstrip().startswith("full_path = ")


def test_nova_omitted_line_falls_back_to_group_first_line(tmp_path: Path) -> None:
    """When Nova doesn't return `line`, the consolidator must fall back to group anchor."""
    # Arrange
    findings = [_security_finding(agent="maya", line=86, confidence=0.85)]
    group = SynthesisGroup(
        findings=findings,
        file_path="src/revue/core/tools/read_file.py",
        line_range=(86, 86),
        group_type="singleton",
    )

    import json
    nova_json = json.dumps([{
        "file": "src/revue/core/tools/read_file.py",
        # NOTE: no "line" field — Nova omitted it
        "issue": "concern",
        "suggestion": "fix",
        "severity": "medium",
    }])

    client = MagicMock()
    client.complete_with_tools.return_value = _completion(nova_json)
    strategy = NovaSingleShotStrategy(
        ai_client=client,
        diff_by_file={"src/revue/core/tools/read_file.py": _READ_FILE_DIFF},
        repo_root=tmp_path,
    )

    # Act
    result = strategy.synthesise(group)

    # Assert — Nova omitted, so group's anchor wins
    assert result.line_number == 86

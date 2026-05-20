"""Tests for OrphanLineGuardPostProcessor (REVUE-249).

The guard is the deterministic backstop for §D4 of the anchor-correction
ADR. It runs AFTER VexVerifyPostProcessor and inspects every finding that
still carries a multi-line ``code_replacement``: if the next non-blank line
below the replacement range is at or deeper than the deepest indent inside
the range, the block continues past the patch and applying it would orphan
the trailing lines.

Both regressions surfaced by PR #29 (REVUE-247) — a nested-conditional
function whose range under-reached by one line, and a string-concat loop
whose range under-reached by two lines — are caught here without LLM
judgement, so the guard cannot regress when the Vex model is swapped.

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from revue_core.comments._orphan_line_guard import OrphanLineGuardPostProcessor
from revue_core.comments.models import Attribution, ConsolidatedFinding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _consolidated(
    *,
    file_path: str = "src/example.py",
    line_number: int = 10,
    code_replacement: "list[str] | None" = None,
    replacement_line_count: int = 1,
) -> ConsolidatedFinding:
    return ConsolidatedFinding(
        file_path=file_path,
        line_number=line_number,
        severity="medium",
        issue="block under-reaches terminator",
        suggestion="rewrite the block as a flat early-return",
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
        snippet="",
        group_type="singleton",
    )


def _capture_nova_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture Log.nova.warning messages dispatched by the guard module."""
    from revue_core.comments import _orphan_line_guard as guard_mod

    captured: list[str] = []
    monkeypatch.setattr(
        guard_mod.Log.nova,
        "warning",
        lambda msg, *args, **kwargs: captured.append(msg % args if args else msg),
    )
    return captured


def _capture_nova_infos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture Log.nova.info messages dispatched by the guard module."""
    from revue_core.comments import _orphan_line_guard as guard_mod

    captured: list[str] = []
    monkeypatch.setattr(
        guard_mod.Log.nova,
        "info",
        lambda msg, *args, **kwargs: captured.append(msg % args if args else msg),
    )
    return captured


# ---------------------------------------------------------------------------
# Task 2 — scaffold (AC4)
# ---------------------------------------------------------------------------


def test_guard_counter_starts_at_zero(tmp_path: Path) -> None:
    """A freshly constructed guard reports zero downgrades — the counter is the
    public observability surface and its initial value is part of the contract.
    """
    # Arrange / Act
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={"src/example.py": "@@ -0,0 +1,1 @@\n+x = 1\n"},
    )

    # Assert
    assert guard.guard_downgrade == 0


# ---------------------------------------------------------------------------
# Task 3 — downgrade algorithm + PR #29 fixtures (AC5, AC6, AC8, AC15, AC16)
# ---------------------------------------------------------------------------


# PR #29 Case 1: a deeply-nested-conditional function whose ``code_replacement``
# stops at line 33, orphaning the final ``return True`` on line 34. Lines 1–19
# are unrelated context; the function spans lines 20–34.
_PR29_CASE_1_FILE = (
    "# header\n"            # line 1
    "# header\n"            # line 2
    "# header\n"            # line 3
    "# header\n"            # line 4
    "# header\n"            # line 5
    "# header\n"            # line 6
    "# header\n"            # line 7
    "# header\n"            # line 8
    "# header\n"            # line 9
    "# header\n"            # line 10
    "# header\n"            # line 11
    "# header\n"            # line 12
    "# header\n"            # line 13
    "# header\n"            # line 14
    "# header\n"            # line 15
    "# header\n"            # line 16
    "# header\n"            # line 17
    "# header\n"            # line 18
    "# header\n"            # line 19
    "def is_eligible(age, income, employed):\n"            # line 20
    "    if employed:\n"                                    # line 21 ← range start
    "        if age >= 18:\n"                               # line 22
    "            if income >= 30000:\n"                     # line 23
    "                if income < 100000:\n"                 # line 24
    "                    return True\n"                     # line 25
    "                else:\n"                               # line 26
    "                    return True\n"                     # line 27
    "            else:\n"                                   # line 28
    "                return False\n"                        # line 29
    "        else:\n"                                       # line 30
    "            return False\n"                            # line 31
    "    else:\n"                                           # line 32
    "        return False\n"                                # line 33 ← range end
    "    return True\n"                                     # line 34 ← orphan
)


# PR #29 Case 2: a string-concatenation loop whose ``code_replacement`` covers
# lines 38–40 (init + loop header + first body statement) and orphans the
# second body statement on line 41 and the final return on line 42.
_PR29_CASE_2_FILE = (
    "# header\n"             # line 1
    "# header\n"             # line 2
    "# header\n"             # line 3
    "# header\n"             # line 4
    "# header\n"             # line 5
    "# header\n"             # line 6
    "# header\n"             # line 7
    "# header\n"             # line 8
    "# header\n"             # line 9
    "# header\n"             # line 10
    "# header\n"             # line 11
    "# header\n"             # line 12
    "# header\n"             # line 13
    "# header\n"             # line 14
    "# header\n"             # line 15
    "# header\n"             # line 16
    "# header\n"             # line 17
    "# header\n"             # line 18
    "# header\n"             # line 19
    "# header\n"             # line 20
    "# header\n"             # line 21
    "# header\n"             # line 22
    "# header\n"             # line 23
    "# header\n"             # line 24
    "# header\n"             # line 25
    "# header\n"             # line 26
    "# header\n"             # line 27
    "# header\n"             # line 28
    "# header\n"             # line 29
    "# header\n"             # line 30
    "# header\n"             # line 31
    "# header\n"             # line 32
    "# header\n"             # line 33
    "# header\n"             # line 34
    "# header\n"             # line 35
    "# header\n"             # line 36
    "def join_strings(items):\n"               # line 37
    "    result = \"\"\n"                        # line 38 ← range start
    "    for item in items:\n"                  # line 39
    "        result = result + item\n"          # line 40 ← range end
    "        result = result + \", \"\n"        # line 41 ← orphan (loop body)
    "    return result\n"                       # line 42 ← orphan (post-loop)
)


def test_guard_downgrades_pr29_case_1_nested_conditional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR §D4 / AC15 — the nested-conditional under-reach from PR #29.

    The replacement covers lines 21–33 but ``return True`` on line 34 is at
    the function-body indent (4 spaces) and the deepest line inside the range
    is at indent 8. The line at end+1 (line 34) is therefore at indent 4,
    which is STRICTLY OUTDENTED from the deepest range indent — accept?

    Not quite. The outermost indent across the range is the indent of line 21
    (``if employed:`` at 4 spaces). Line 34 at indent 4 equals outermost_indent,
    so the block continues at the same level as the range's outermost line —
    the function body is not done. Downgrade.
    """
    # Arrange
    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_1_FILE)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=21,
        replacement_line_count=13,  # lines 21..33 inclusive
        code_replacement=[
            "    if not employed:",
            "        return False",
            "    if age < 18:",
            "        return False",
            "    if income < 30000:",
            "        return False",
            "    if income < 100000:",
            "        return True",
            "    return True",
        ],
    )
    captured_warnings = _capture_nova_warnings(monkeypatch)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,34 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert — finding survives, but with code_replacement stripped.
    assert out is not None
    assert out.code_replacement is None
    assert out.replacement_line_count == 1
    assert out.snippet == ""
    # Prose fields are preserved so the comment still posts.
    assert out.issue == finding.issue
    assert out.suggestion == finding.suggestion
    # Counter incremented exactly once.
    assert guard.guard_downgrade == 1
    # WARN log fired with the structured prefix.
    assert any("[orphan-guard-downgrade]" in w for w in captured_warnings), (
        f"expected [orphan-guard-downgrade] in warnings, got {captured_warnings}"
    )


def test_guard_downgrades_pr29_case_2_loop_under_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR §D4 / AC16 — the string-concat loop under-reach from PR #29.

    The replacement covers lines 38–40. The trailing inspection line is 41
    (``result = result + ", "``) at indent 8, which equals the deepest indent
    inside the range (line 40 at indent 8). Downgrade.
    """
    # Arrange
    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_2_FILE)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=38,
        replacement_line_count=3,  # lines 38..40 inclusive
        code_replacement=[
            "    return \"\".join(items)",
        ],
    )
    captured_warnings = _capture_nova_warnings(monkeypatch)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,42 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is not None
    assert out.code_replacement is None
    assert out.replacement_line_count == 1
    assert guard.guard_downgrade == 1
    assert any("[orphan-guard-downgrade]" in w for w in captured_warnings)


def test_guard_warn_log_includes_file_line_and_indent_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC8 — the WARN log message must carry the file path, the end+1 line
    number, and both indents in a grep-friendly key=value format.
    """
    # Arrange
    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_2_FILE)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=38,
        replacement_line_count=3,
        code_replacement=["    return \"\".join(items)"],
    )
    captured_warnings = _capture_nova_warnings(monkeypatch)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,42 @@\n"},
    )

    # Act
    guard.process(finding)

    # Assert
    warning_line = next(
        (w for w in captured_warnings if "[orphan-guard-downgrade]" in w), None
    )
    assert warning_line is not None
    assert file_path_rel in warning_line
    # end_line is 40 → end_line + 1 == 41 is the inspection line.
    assert "41" in warning_line
    # The two indent metrics must be reported so dogfood can compare them.
    assert "trailing_indent=" in warning_line
    assert "outermost_indent=" in warning_line


# ---------------------------------------------------------------------------
# Task 4 — negative cases (AC17, AC18, AC19, AC20, AC21, AC22)
# ---------------------------------------------------------------------------


def test_guard_accepts_when_replacement_covers_the_full_block(tmp_path: Path) -> None:
    """AC17 — replacement covers the entire function span; line after the
    range is at file-level indent (outdented from the function body) so the
    block terminated cleanly. Finding passes through unmodified.
    """
    # Arrange
    file_content = (
        "def f():\n"               # line 1
        "    if cond:\n"           # line 2 ← range start
        "        return True\n"    # line 3
        "    return False\n"       # line 4 ← range end (function ends)
        "\n"                        # line 5 blank
        "x = 1\n"                  # line 6 ← module level, indent 0
    )
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(file_content)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=2,
        replacement_line_count=3,  # covers 2..4 inclusive — whole function body
        code_replacement=[
            "    return cond",
        ],
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,6 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is finding
    assert out.code_replacement == finding.code_replacement
    assert guard.guard_downgrade == 0


def test_guard_passes_through_prose_only_finding(tmp_path: Path) -> None:
    """AC18 — finding with no code_replacement skips inspection entirely.
    The guard never reads the file in this case.
    """
    # Arrange
    finding = _consolidated(
        file_path="src/example.py",
        code_replacement=None,
        replacement_line_count=1,
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={"src/example.py": "@@ -0,0 +1,1 @@\n+x = 1\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is finding
    assert guard.guard_downgrade == 0


def test_guard_passes_through_finding_already_downgraded_by_vex(tmp_path: Path) -> None:
    """AC19 — Vex's drop_cr_keep_prose mutation produces a finding with
    code_replacement=None and replacement_line_count=1. The guard treats it
    the same as any prose-only finding.
    """
    # Arrange
    finding = _consolidated(
        file_path="src/example.py",
        code_replacement=None,   # Vex stripped it
        replacement_line_count=1,
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={"src/example.py": "@@ -0,0 +1,1 @@\n+x = 1\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is finding
    assert guard.guard_downgrade == 0


def test_guard_passes_through_single_line_replacement(tmp_path: Path) -> None:
    """A single-line replacement cannot under-reach a block — the patch is
    one line, so there is no 'range' beyond the anchor itself. Skip without
    reading the file.
    """
    # Arrange
    finding = _consolidated(
        file_path="src/example.py",
        line_number=10,
        replacement_line_count=1,
        code_replacement=["    return value"],
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={"src/example.py": "@@ -0,0 +1,1 @@\n+x = 1\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is finding
    assert guard.guard_downgrade == 0


def test_guard_accepts_when_replacement_reaches_end_of_file(tmp_path: Path) -> None:
    """AC20 — replacement extends to the last non-blank line; end_line + 1
    is past EOF. No trailing line to compare against → accept.
    """
    # Arrange
    file_content = (
        "def f():\n"                # line 1
        "    if cond:\n"            # line 2 ← range start
        "        return True\n"     # line 3
        "    return False\n"        # line 4 ← range end == EOF (no trailing blank)
    )
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(file_content)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=2,
        replacement_line_count=3,
        code_replacement=["    return cond"],
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,4 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is finding
    assert guard.guard_downgrade == 0


def test_guard_accepts_blank_trailing_line_when_following_line_is_outdented(
    tmp_path: Path,
) -> None:
    """AC21 — the immediate next line is blank (typical end-of-function); the
    line after that is outdented (module scope). The block terminated; accept.
    """
    # Arrange
    file_content = (
        "def f():\n"                # line 1
        "    if cond:\n"            # line 2 ← range start
        "        return True\n"     # line 3
        "    return False\n"        # line 4 ← range end
        "\n"                         # line 5 ← blank (skipped while probing)
        "def g():\n"                # line 6 ← module scope, outdented
        "    pass\n"                # line 7
    )
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(file_content)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=2,
        replacement_line_count=3,
        code_replacement=["    return cond"],
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,7 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is finding
    assert guard.guard_downgrade == 0


def test_guard_downgrades_when_blank_trailing_line_precedes_an_orphan(
    tmp_path: Path,
) -> None:
    """AC22 — the immediate next line is blank, but the line after that is
    still at or deeper than outermost_indent. The block continues past the
    blank; downgrade.
    """
    # Arrange
    file_content = (
        "def f():\n"                 # line 1
        "    if cond:\n"             # line 2 ← range start
        "        return True\n"      # line 3 ← range end
        "\n"                          # line 4 ← blank (skipped)
        "    return False\n"         # line 5 ← orphan at function-body indent
    )
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(file_content)

    finding = _consolidated(
        file_path=file_path_rel,
        line_number=2,
        replacement_line_count=2,
        code_replacement=["    return cond and True"],
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,5 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert
    assert out is not None
    assert out.code_replacement is None
    assert out.replacement_line_count == 1
    assert guard.guard_downgrade == 1


# ---------------------------------------------------------------------------
# Task 5 — read-error fallback (AC10, AC23)
# ---------------------------------------------------------------------------


def test_guard_fails_open_when_read_tool_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC10 / AC23 — file in the allowlist but absent on disk; ReadFileTool
    returns is_error=True. The guard's blast radius must not exceed the bug
    it was added to catch, so the finding passes through unchanged and an
    INFO log records the skip.
    """
    # Arrange — the path is in diff_by_file (so the sandbox accepts it) but
    # nothing was written to disk, so the read fails.
    file_path_rel = "src/missing.py"
    finding = _consolidated(
        file_path=file_path_rel,
        line_number=10,
        replacement_line_count=3,
        code_replacement=["    return True", "    return False"],
    )
    captured_infos = _capture_nova_infos(monkeypatch)
    captured_warnings = _capture_nova_warnings(monkeypatch)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,1 @@\n"},
    )

    # Act
    out = guard.process(finding)

    # Assert — finding unchanged, no downgrade, INFO log records the skip.
    assert out is finding
    assert out.code_replacement == finding.code_replacement
    assert guard.guard_downgrade == 0
    # No WARN — downgrade did not happen.
    assert not any("[orphan-guard-downgrade]" in w for w in captured_warnings)
    # AC10 — INFO log shape is ``[orphan-guard-failure] read_error file:line: <error>``.
    # Both the file path AND the line number must be present so dogfood greps
    # can tie the failure back to the finding that triggered it.
    failure_line = next(
        (m for m in captured_infos if "[orphan-guard-failure]" in m), None
    )
    assert failure_line is not None, (
        f"expected [orphan-guard-failure] in infos, got {captured_infos}"
    )
    assert file_path_rel in failure_line
    # AC10 spec: file:line: format — the finding's line_number must appear.
    assert f"{file_path_rel}:{finding.line_number}:" in failure_line, (
        f"expected '{file_path_rel}:{finding.line_number}:' in '{failure_line}'"
    )


# ---------------------------------------------------------------------------
# REVUE-249 — Vex widen-and-apply contract (consumer regression guard)
# ---------------------------------------------------------------------------


def test_vex_widen_and_apply_flows_through_guard_when_widened_range_covers_the_block(
    tmp_path: Path,
) -> None:
    """The new block-completeness subsection tells Vex it has two outputs when
    a multi-line replacement under-reaches a block: widen the range via
    ``corrected_anchor.replacement_line_count``, or downgrade to
    ``drop_cr_keep_prose``. This regression guard locks the widen path in
    place by exercising it end-to-end:

      1. The agent emits a too-short ``replacement_line_count``.
      2. Vex returns ``apply`` with a ``corrected_anchor`` that widens the
         range to cover the natural block terminator.
      3. ``VexVerifyPostProcessor`` consumes BOTH ``corrected_anchor.line``
         and ``corrected_anchor.replacement_line_count`` (REVUE-248 wired
         this — pinned by ``_verifier.py:_apply_verdict`` and
         ``_sanitize_correction``).
      4. The widened finding flows into ``OrphanLineGuardPostProcessor``;
         since Vex widened correctly, the guard inspects line end+1 (now
         outdented) and accepts.

    If a future refactor drops ``replacement_line_count`` from the
    ``_apply_verdict`` rebase, this test fails first — without the widen,
    the guard would see the original short range and downgrade, masking the
    contract regression behind a "looks like the guard fired correctly"
    metric. The assertion below specifically locks the *Vex-widened* path,
    not the guard-catches-everything fallback.
    """
    # Arrange — file containing a complete function whose body ends at line 4.
    # Line 5 is module-scope (outdented) so the guard accepts when the range
    # covers lines 2..4 inclusive; it would downgrade if the range stopped at
    # line 3 (next non-blank line at line 4 has the same indent as line 3).
    from revue_core.comments._verifier import (
        CorrectedAnchor,
        VexVerdict,
        VexVerifyPostProcessor,
    )
    from unittest.mock import MagicMock

    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(
        "def f(cond):\n"          # line 1
        "    if cond:\n"          # line 2 ← agent's reported start
        "        return True\n"   # line 3 ← agent's (too short) end
        "    return False\n"      # line 4 ← the natural block terminator
        "\n"                       # line 5 blank
        "x = 1\n"                 # line 6 ← module scope (outdented)
    )

    # Agent reported rlc=2 → covers lines 2..3, leaving line 4 orphaned.
    # Vex widens to rlc=3 via corrected_anchor → covers lines 2..4.
    finding = _consolidated(
        file_path=file_path_rel,
        line_number=2,
        replacement_line_count=2,
        code_replacement=["    return cond"],
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="Range widened to cover the natural block terminator on line 4.",
        corrected_anchor=CorrectedAnchor(line=2, replacement_line_count=3),
    )
    # Diff must cover both reported and corrected positions so PositionAdapter
    # re-validation accepts the correction.
    diff = "@@ -0,0 +1,6 @@\n" + "".join(f"+line {i + 1}\n" for i in range(6))

    vex_pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_path_rel: diff},
    )
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: diff},
    )

    # Act — run the post-processors in chain order, exactly as the
    # consolidator wires them.
    after_vex = vex_pp.process(finding)
    assert after_vex is not None  # apply does not drop the finding
    after_guard = guard.process(after_vex)

    # Assert — Vex widened the range (line 748 of _verifier.py); the guard
    # then accepted because the widened range reaches the block terminator.
    # If the consumer regresses on rlc, this assertion fails — the guard
    # would downgrade and ``after_guard.code_replacement`` would be None.
    assert after_vex.replacement_line_count == 3, (
        "VexVerifyPostProcessor must apply corrected_anchor.replacement_line_count "
        "on the 'apply' path (verifier.py:_apply_verdict). If this fails, the "
        "widen-and-apply contract from REVUE-248 has regressed."
    )
    assert after_guard is not None
    assert after_guard.code_replacement == ["    return cond"]
    assert after_guard.replacement_line_count == 3
    assert guard.guard_downgrade == 0


# ---------------------------------------------------------------------------
# Task 6 — counter + thread safety (AC7, AC9, AC24)
# ---------------------------------------------------------------------------


def test_guard_counter_increments_once_per_downgrade_across_multi_finding_batch(
    tmp_path: Path,
) -> None:
    """AC24 — across a multi-finding batch where exactly N findings should
    downgrade, the counter ends at N. Guards against double-increment and
    accidental decrement bugs.
    """
    # Arrange — three findings on the same loop-orphan file.
    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_2_FILE)

    findings = [
        _consolidated(
            file_path=file_path_rel,
            line_number=38,
            replacement_line_count=3,
            code_replacement=["    return \"\".join(items)"],
        ),
        # A prose-only finding should not affect the counter.
        _consolidated(
            file_path=file_path_rel,
            line_number=40,
            replacement_line_count=1,
            code_replacement=None,
        ),
        # Another genuine under-reach.
        _consolidated(
            file_path=file_path_rel,
            line_number=38,
            replacement_line_count=3,
            code_replacement=["    return \"\".join(items)"],
        ),
    ]
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,42 @@\n"},
    )

    # Act
    results = [guard.process(f) for f in findings]

    # Assert
    assert guard.guard_downgrade == 2
    # The prose-only finding passed through untouched.
    assert results[1] is findings[1]


def test_guard_counter_is_thread_safe_under_concurrent_process_calls(
    tmp_path: Path,
) -> None:
    """AC9 — the counter mutation is protected by a lock. Without the lock,
    the read-modify-write of ``self._guard_downgrade`` races and drops
    increments under enough threads. This regression test fires concurrent
    process() calls and asserts the final counter equals the expected total.
    """
    # Arrange — every finding will downgrade, so the expected counter is the
    # number of findings.
    from concurrent.futures import ThreadPoolExecutor

    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_2_FILE)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,42 @@\n"},
    )
    findings = [
        _consolidated(
            file_path=file_path_rel,
            line_number=38,
            replacement_line_count=3,
            code_replacement=["    return \"\".join(items)"],
        )
        for _ in range(40)
    ]

    # Act
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(guard.process, findings))

    # Assert
    assert guard.guard_downgrade == len(findings)


def test_guard_caches_file_content_across_findings_on_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PR with N findings on the same file should incur exactly one
    filesystem read. Without caching, ReadFileTool.execute fires N times,
    re-allocating the file string and the split-lines list each time.
    """
    # Arrange — three findings on the same file.
    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_2_FILE)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,42 @@\n"},
    )
    # Wrap the underlying read tool so we can count invocations.
    real_execute = guard._read_tool.execute
    call_count = {"n": 0}

    def counting_execute(*, path: str):
        call_count["n"] += 1
        return real_execute(path=path)

    monkeypatch.setattr(guard._read_tool, "execute", counting_execute)

    findings = [
        _consolidated(
            file_path=file_path_rel,
            line_number=38,
            replacement_line_count=3,
            code_replacement=["    return \"\".join(items)"],
        )
        for _ in range(3)
    ]

    # Act
    for finding in findings:
        guard.process(finding)

    # Assert — three findings, one filesystem read.
    assert call_count["n"] == 1
    # Behaviour preserved: every finding was still downgraded.
    assert guard.guard_downgrade == 3


def test_guard_caches_read_errors_so_a_failing_file_logs_and_reads_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file whose ReadFileTool call errors is a steady-state failure for
    the lifetime of the review run — caching the failure keeps the second
    finding on the same file from emitting a duplicate INFO line and from
    paying the cost of a redundant filesystem syscall.
    """
    # Arrange — the file is in the allowlist but does not exist on disk, so
    # ReadFileTool.execute returns an error result on every call.
    file_path_rel = "src/missing.py"
    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,10 @@\n"},
    )
    real_execute = guard._read_tool.execute
    call_count = {"n": 0}

    def counting_execute(*, path: str):
        call_count["n"] += 1
        return real_execute(path=path)

    monkeypatch.setattr(guard._read_tool, "execute", counting_execute)
    captured_infos = _capture_nova_infos(monkeypatch)

    findings = [
        _consolidated(
            file_path=file_path_rel,
            line_number=2,
            replacement_line_count=3,
            code_replacement=["    return True"],
        )
        for _ in range(3)
    ]

    # Act
    results = [guard.process(f) for f in findings]

    # Assert — one read attempt, one log line, every finding passes through.
    assert call_count["n"] == 1
    assert sum(1 for line in captured_infos if "[orphan-guard-failure]" in line) == 1
    assert all(r is f for r, f in zip(results, findings))


def test_guard_holds_lock_across_read_to_prevent_toctou_double_read(
    tmp_path: Path,
) -> None:
    """Under concurrent first-touch on the same file, the cache must serve
    exactly one filesystem read. A check-then-read pattern that releases the
    lock between miss-check and write would let two threads both miss and
    both call execute() — wasting I/O and breaking the documented contract.
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading as _threading

    file_path_rel = "src/sample_module.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text(_PR29_CASE_2_FILE)

    guard = OrphanLineGuardPostProcessor(
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,42 @@\n"},
    )

    # Slow the underlying read so multiple threads enter the load path
    # simultaneously — if the lock is released between miss-check and write,
    # the race window is wide enough for several threads to all call execute.
    real_execute = guard._read_tool.execute
    call_count = {"n": 0}
    count_lock = _threading.Lock()
    release_gate = _threading.Event()

    def slow_execute(*, path: str):
        with count_lock:
            call_count["n"] += 1
        release_gate.wait(timeout=2.0)
        return real_execute(path=path)

    import threading
    threading.Thread(target=lambda: (
        # let workers stack up on the lock before the first read returns
        threading.Event().wait(0.05),
        release_gate.set(),
    )).start()

    guard._read_tool.execute = slow_execute  # type: ignore[assignment]

    findings = [
        _consolidated(
            file_path=file_path_rel,
            line_number=38,
            replacement_line_count=3,
            code_replacement=["    return \"\".join(items)"],
        )
        for _ in range(8)
    ]

    # Act
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(guard.process, findings))

    # Assert — exactly one underlying read, regardless of thread contention.
    assert call_count["n"] == 1
    assert guard.guard_downgrade == len(findings)

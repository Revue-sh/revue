"""Tests for VexVerifyPostProcessor (REVUE-240).

The post-processor sits in the Consolidator's post-processor chain. It reads
the file at HEAD, invokes VexVerifier, and applies the verdict to the
ConsolidatedFinding:

  - apply              → return finding unchanged
  - drop_cr_keep_prose → return finding with code_replacement=None, rlc=1
  - reject_finding     → return None (consolidator drops it from the stream)

Each test follows AAA structure (Arrange / Act / Assert).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from revue.comments._verifier import VexVerdict, VexVerifyPostProcessor
from revue.comments.models import Attribution, ConsolidatedFinding


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
        issue="missing input validation",
        suggestion="raise ValueError when input is None",
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="maya", category="code-quality")],
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
        snippet="",
        group_type="singleton",
    )


# ---------------------------------------------------------------------------
# Short-circuit — no code_replacement, no Vex call
# ---------------------------------------------------------------------------


def test_returns_finding_unchanged_when_finding_has_no_code_replacement(tmp_path: Path) -> None:
    """Prose-only findings need no verification — Vex isn't invoked."""
    # Arrange
    finding = _consolidated(code_replacement=None)
    verifier = MagicMock()
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={"src/example.py": "@@ -0,0 +10,1 @@\n+x = 1\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is finding
    verifier.verify.assert_not_called()


# ---------------------------------------------------------------------------
# Apply verdict — finding passes through untouched
# ---------------------------------------------------------------------------


def test_returns_finding_unchanged_when_vex_returns_apply_verdict(tmp_path: Path) -> None:
    """Vex says 'apply' — post-processor returns the finding without modification."""
    # Arrange
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text("def shipped():\n    return True\n")

    finding = _consolidated(
        file_path=file_path_rel,
        code_replacement=["    return False"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(verdict="apply", reason="safe")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,2 @@\n+def shipped():\n+    return True\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.code_replacement == ["    return False"]
    assert out.replacement_line_count == 1
    verifier.verify.assert_called_once()


# ---------------------------------------------------------------------------
# drop_cr_keep_prose verdict — strip code_replacement, keep prose
# ---------------------------------------------------------------------------


def test_strips_code_replacement_when_vex_returns_drop_cr_keep_prose(tmp_path: Path) -> None:
    """Vex says the patch is unsafe — keep the prose, drop the suggestion fence."""
    # Arrange
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text("def shipped():\n    return True\n")

    finding = _consolidated(
        file_path=file_path_rel,
        code_replacement=["    return False"],
        replacement_line_count=3,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(verdict="drop_cr_keep_prose", reason="indent mismatch")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,2 @@\n+def shipped():\n+    return True\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert — prose preserved, code_replacement dropped, rlc reset to 1
    assert out is not None
    assert out.code_replacement is None
    assert out.replacement_line_count == 1
    assert out.issue == "missing input validation"  # prose still present
    assert out.suggestion == "raise ValueError when input is None"


# ---------------------------------------------------------------------------
# reject_finding verdict — return None so consolidator drops it
# ---------------------------------------------------------------------------


def test_returns_none_when_vex_returns_reject_finding(tmp_path: Path) -> None:
    """Vex says the finding itself is wrong — drop it from the inline stream."""
    # Arrange
    file_path_rel = "src/example.py"
    target = tmp_path / file_path_rel
    target.parent.mkdir(parents=True)
    target.write_text("if (!id) throw new Error('id required');\n")

    finding = _consolidated(
        file_path=file_path_rel,
        code_replacement=["if (!id) throw new Error('id required');"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="reject_finding", reason="already addressed in code"
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_path_rel: "@@ -0,0 +1,1 @@\n+if (!id) throw new Error('id required');\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is None


# ---------------------------------------------------------------------------
# File read failures — fail open (keep finding) so Vex doesn't block reviews
# ---------------------------------------------------------------------------


def test_keeps_finding_unchanged_when_file_cannot_be_read(tmp_path: Path) -> None:
    """File doesn't exist or is too large: skip Vex, keep the finding as-is.

    Vex's failure mode must not silently strip suggestions — that would make
    Vex's blast radius wider than the bug it was added to fix.
    """
    # Arrange — file_path is in diff_by_file but doesn't exist on disk
    finding = _consolidated(
        file_path="src/missing.py",
        code_replacement=["x = 1"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={"src/missing.py": "@@ -0,0 +1,1 @@\n+x = 1\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert — finding preserved, Vex not even invoked
    assert out is finding
    verifier.verify.assert_not_called()


# ---------------------------------------------------------------------------
# P7 — drop_cr_keep_prose clears snippet (stale snippet would describe rejected span)
# ---------------------------------------------------------------------------


def test_drop_cr_keep_prose_clears_snippet_in_addition_to_code_replacement(tmp_path: Path) -> None:
    """Vex rejects the patch — the snippet quoting that patch must go too."""
    # Arrange
    from revue.comments._verifier import VexVerdict
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    finding = _consolidated(
        file_path=file_rel,
        code_replacement=["bad replacement"],
        replacement_line_count=3,
    )
    finding = ConsolidatedFinding(
        file_path=finding.file_path,
        line_number=finding.line_number,
        severity=finding.severity,
        issue=finding.issue,
        suggestion=finding.suggestion,
        confidence=finding.confidence,
        category=finding.category,
        attribution=finding.attribution,
        code_replacement=finding.code_replacement,
        replacement_line_count=finding.replacement_line_count,
        snippet="stale snippet from rejected span",
        group_type=finding.group_type,
    )

    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(verdict="drop_cr_keep_prose", reason="x")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.snippet == ""


# ---------------------------------------------------------------------------
# P11 — corrected_anchor on apply verdict repositions the finding
# ---------------------------------------------------------------------------


def test_apply_with_corrected_anchor_repositions_line_and_rlc(tmp_path: Path) -> None:
    """Vex says 'apply, but starting at line 91 with rlc=4': finding gets the corrected span."""
    # Arrange
    from revue.comments._verifier import CorrectedAnchor, VexVerdict
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    finding = _consolidated(
        file_path=file_rel,
        line_number=86,
        code_replacement=["fixed line 1", "fixed line 2", "fixed line 3", "fixed line 4"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="Content sound, span moved",
        corrected_anchor=CorrectedAnchor(line=91, replacement_line_count=4),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.line_number == 91
    assert out.replacement_line_count == 4
    # code_replacement preserved verbatim
    assert out.code_replacement == ["fixed line 1", "fixed line 2", "fixed line 3", "fixed line 4"]


def test_counters_track_each_verdict_type_and_each_failure_mode(tmp_path: Path) -> None:
    """P8: process records counts per verdict and per failure mode."""
    # Arrange — 4 findings: one apply, one drop, one reject, one read_error
    from revue.comments._verifier import VexVerdict
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")
    missing_rel = "src/missing.py"  # in diff but not on disk → read_error

    verifier = MagicMock()
    verifier.verify.side_effect = [
        VexVerdict(verdict="apply", reason="ok"),
        VexVerdict(verdict="drop_cr_keep_prose", reason="anchor wrong"),
        VexVerdict(verdict="reject_finding", reason="already done"),
    ]
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={
            file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n",
            missing_rel: "@@ -0,0 +1,1 @@\n+x\n",
        },
    )
    findings = [
        _consolidated(file_path=file_rel, line_number=1, code_replacement=["a"]),
        _consolidated(file_path=file_rel, line_number=2, code_replacement=["b"]),
        _consolidated(file_path=file_rel, line_number=3, code_replacement=["c"]),
        _consolidated(file_path=missing_rel, line_number=1, code_replacement=["d"]),
    ]

    # Act
    for f in findings:
        pp.process(f)

    # Assert
    assert pp.verdict_counts == {"apply": 1, "drop_cr_keep_prose": 1, "reject_finding": 1}
    assert pp.failure_counts["read_error"] == 1
    assert pp.failure_counts["no_code_replacement"] == 0
    assert pp.failure_counts["verifier_exception"] == 0


def test_counters_track_verifier_exception_under_fail_open(tmp_path: Path) -> None:
    """When the verifier itself raises (rate limit, network), bump verifier_exception and keep finding."""
    # Arrange
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    verifier = MagicMock()
    verifier.verify.side_effect = RuntimeError("simulated rate limit")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )
    finding = _consolidated(file_path=file_rel, line_number=1, code_replacement=["x"])

    # Act
    out = pp.process(finding)

    # Assert — finding preserved unchanged AND counter incremented
    assert out is finding
    assert pp.failure_counts["verifier_exception"] == 1
    assert pp.verdict_counts == {"apply": 0, "drop_cr_keep_prose": 0, "reject_finding": 0}


def test_process_all_runs_findings_in_parallel_up_to_max_workers(tmp_path: Path) -> None:
    """process_all() must call verify() concurrently for N findings, not serially."""
    # Arrange — 3 findings, each verifier call blocks for ~50ms; max_workers=3
    # should complete all three in ~50ms rather than ~150ms sequential
    from revue.comments._verifier import VexVerdict
    import time

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    findings = [
        _consolidated(file_path=file_rel, line_number=10 + i, code_replacement=[f"x = {i}"])
        for i in range(3)
    ]

    def _slow_verify(*, file_content, finding):
        time.sleep(0.05)
        return VexVerdict(verdict="apply", reason="ok")

    verifier = MagicMock()
    verifier.verify.side_effect = _slow_verify
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
        max_workers=3,
    )

    # Act
    start = time.monotonic()
    results = pp.process_all(findings)
    elapsed = time.monotonic() - start

    # Assert — order preserved, all three findings processed
    assert len(results) == 3
    assert all(r is not None for r in results)
    # Parallel: ≈0.05s. Sequential would be ≈0.15s. Allow generous headroom.
    assert elapsed < 0.12, (
        f"process_all took {elapsed:.3f}s — expected ~0.05s with 3-way parallelism, "
        f"~0.15s if running sequentially."
    )


def test_drop_cr_keep_prose_with_corrected_anchor_repositions_prose_to_correct_line(
    tmp_path: Path,
) -> None:
    """Vex rejects the patch and tells us where the prose should anchor instead."""
    # Arrange
    from revue.comments._verifier import CorrectedAnchor, VexVerdict
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    finding = _consolidated(
        file_path=file_rel,
        line_number=86,
        code_replacement=["wrong span"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="drop_cr_keep_prose",
        reason="Wrong anchor; the real concern is on line 91",
        corrected_anchor=CorrectedAnchor(line=91, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert — prose repositioned, suggestion dropped
    assert out is not None
    assert out.line_number == 91
    assert out.code_replacement is None
    assert out.replacement_line_count == 1

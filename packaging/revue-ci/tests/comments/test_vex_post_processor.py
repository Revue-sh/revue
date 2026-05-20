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

from revue_core.comments._verifier import VexVerdict, VexVerifyPostProcessor
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
    from revue_core.comments._verifier import VexVerdict
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
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict
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
    # Diff covers reported line 86 AND corrected line 91 so re-validation accepts.
    diff = "@@ -0,0 +86,10 @@\n" + "".join(f"+line {86 + i}\n" for i in range(10))
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
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
    from revue_core.comments._verifier import VexVerdict
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
    assert pp.failure_counts["other"] == 0


def test_counters_track_verifier_exception_under_fail_open(tmp_path: Path) -> None:
    """When the verifier raises a generic exception (no special class), bump 'other' and keep finding."""
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

    # Assert — finding preserved unchanged AND 'other' counter incremented
    assert out is finding
    assert pp.failure_counts["other"] == 1
    assert pp.verdict_counts == {"apply": 0, "drop_cr_keep_prose": 0, "reject_finding": 0}


def test_process_all_runs_findings_in_parallel_up_to_max_workers(tmp_path: Path) -> None:
    """process_all() must call verify() concurrently for N findings, not serially."""
    # Arrange — 3 findings, each verifier call blocks for ~50ms; max_workers=3
    # should complete all three in ~50ms rather than ~150ms sequential
    from revue_core.comments._verifier import VexVerdict
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


# ---------------------------------------------------------------------------
# REVUE-248 — D1 INFO log [vex-anchor-fix] when correction lands
# ---------------------------------------------------------------------------


def test_vex_anchor_fix_info_log_fires_when_correction_changes_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a corrected anchor is accepted and the posted line actually moves,
    emit an INFO log line on the nova channel so dogfood greps surface it.
    """
    # Arrange
    captured_infos = _capture_nova_infos(monkeypatch)
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    diff = "@@ -1,2 +1,5 @@\n line 1\n line 2\n+line 3\n+line 4\n+line 5\n"
    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    pp.process(finding)

    # Assert
    assert any(
        "[vex-anchor-fix]" in m and "4" in m and "5" in m for m in captured_infos
    ), f"expected [vex-anchor-fix] info log, captured: {captured_infos}"


def test_vex_anchor_fix_does_not_fire_when_line_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pure apply (no correction) → no [vex-anchor-fix] log."""
    # Arrange
    captured_infos = _capture_nova_infos(monkeypatch)
    from revue_core.comments._verifier import VexVerdict

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(verdict="apply", reason="safe")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -1,1 +1,2 @@\n line 1\n+line 2\n"},
    )

    # Act
    pp.process(finding)

    # Assert — no anchor-fix log
    assert not any("[vex-anchor-fix]" in m for m in captured_infos)


# ---------------------------------------------------------------------------
# REVUE-248 — D1.e Feature flag REVUE_VEX_CORRECTION_ENABLED
# ---------------------------------------------------------------------------


def test_feature_flag_default_true_applies_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset REVUE_VEX_CORRECTION_ENABLED → default true → correction is applied."""
    # Arrange
    monkeypatch.delenv("REVUE_VEX_CORRECTION_ENABLED", raising=False)
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    diff = "@@ -1,2 +1,5 @@\n line 1\n line 2\n+line 3\n+line 4\n+line 5\n"
    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert — correction applied (default behaviour)
    assert out is not None
    assert out.line_number == 5


def test_feature_flag_false_short_circuits_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REVUE_VEX_CORRECTION_ENABLED=false → corrected_anchor is silently discarded.

    Behaviour must be bit-identical to pre-D1: the finding line_number stays at
    the agent's reported line; no [vex-correction-*] log lines fire.
    """
    # Arrange
    monkeypatch.setenv("REVUE_VEX_CORRECTION_ENABLED", "false")
    captured_warnings = _capture_nova_warnings(monkeypatch)
    captured_infos = _capture_nova_infos(monkeypatch)
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    diff = "@@ -1,2 +1,5 @@\n line 1\n line 2\n+line 3\n+line 4\n+line 5\n"
    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert — correction ignored
    assert out is not None
    assert out.line_number == 4
    # No correction-channel log lines should fire
    correction_logs = [
        m
        for m in captured_warnings + captured_infos
        if "[vex-correction-" in m or "[vex-anchor-" in m
    ]
    assert correction_logs == []


def test_feature_flag_value_is_read_once_at_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing the env var after __init__ must not affect already-constructed post-processors.

    Reading the flag per-finding would be both expensive and surprising — a
    deploy-time toggle should be deploy-time, not per-call.
    """
    # Arrange — construct with flag=false
    monkeypatch.setenv("REVUE_VEX_CORRECTION_ENABLED", "false")
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    diff = "@@ -1,2 +1,5 @@\n line 1\n line 2\n+line 3\n+line 4\n+line 5\n"
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Now flip the flag — should NOT affect pp
    monkeypatch.setenv("REVUE_VEX_CORRECTION_ENABLED", "true")
    finding = _consolidated(file_path=file_rel, line_number=4, code_replacement=["x"])

    # Act
    out = pp.process(finding)

    # Assert — still using the flag from __init__ (false)
    assert out is not None
    assert out.line_number == 4


# ---------------------------------------------------------------------------
# REVUE-248 — D1.d Vex-failure classification (extends failure_counts)
# ---------------------------------------------------------------------------


class _FakeHTTPError(Exception):
    """Stand-in for an SDK HTTPStatusError that exposes ``status_code``."""

    def __init__(self, status_code: int, message: str = "http") -> None:
        super().__init__(message)
        self.status_code = status_code


def test_vex_failure_classified_as_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TimeoutError from verifier → failure_counts['timeout'] += 1 and WARN logs error_type=timeout."""
    # Arrange
    captured_warnings = _capture_nova_warnings(monkeypatch)
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    verifier = MagicMock()
    verifier.verify.side_effect = TimeoutError("connection timed out")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )
    finding = _consolidated(file_path=file_rel, line_number=1, code_replacement=["x"])

    # Act
    out = pp.process(finding)

    # Assert — finding preserved, counter and log routed
    assert out is finding
    assert pp.failure_counts["timeout"] == 1
    assert any("[vex-failure]" in w and "error_type=timeout" in w for w in captured_warnings)


def test_vex_failure_classified_as_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """json.JSONDecodeError from verifier → failure_counts['malformed_json'] += 1."""
    import json as _json

    captured_warnings = _capture_nova_warnings(monkeypatch)
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    verifier = MagicMock()
    verifier.verify.side_effect = _json.JSONDecodeError("expected value", "doc", 0)
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )
    finding = _consolidated(file_path=file_rel, line_number=1, code_replacement=["x"])

    # Act
    out = pp.process(finding)

    # Assert
    assert out is finding
    assert pp.failure_counts["malformed_json"] == 1
    assert any("error_type=malformed_json" in w for w in captured_warnings)


def test_vex_failure_classified_as_http_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception with status_code in 500–599 → failure_counts['http_5xx'] += 1."""
    captured_warnings = _capture_nova_warnings(monkeypatch)
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    verifier = MagicMock()
    verifier.verify.side_effect = _FakeHTTPError(status_code=503, message="service unavailable")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )
    finding = _consolidated(file_path=file_rel, line_number=1, code_replacement=["x"])

    # Act
    out = pp.process(finding)

    # Assert
    assert out is finding
    assert pp.failure_counts["http_5xx"] == 1
    assert any("error_type=http_5xx" in w for w in captured_warnings)


def test_vex_failure_classified_as_http_4xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception with status_code in 400–499 → failure_counts['http_4xx'] += 1."""
    captured_warnings = _capture_nova_warnings(monkeypatch)
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    verifier = MagicMock()
    verifier.verify.side_effect = _FakeHTTPError(status_code=429, message="rate limited")
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )
    finding = _consolidated(file_path=file_rel, line_number=1, code_replacement=["x"])

    # Act
    out = pp.process(finding)

    # Assert
    assert out is finding
    assert pp.failure_counts["http_4xx"] == 1
    assert any("error_type=http_4xx" in w for w in captured_warnings)


def test_vex_failure_message_truncated_to_120_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WARN log truncates very long exception messages so log lines stay scannable."""
    captured_warnings = _capture_nova_warnings(monkeypatch)
    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    long_msg = "x" * 500
    verifier = MagicMock()
    verifier.verify.side_effect = RuntimeError(long_msg)
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +1,1 @@\n+placeholder\n"},
    )
    finding = _consolidated(file_path=file_rel, line_number=1, code_replacement=["x"])

    # Act
    pp.process(finding)

    # Assert — at least one warning contains a truncated message
    vex_failures = [w for w in captured_warnings if "[vex-failure]" in w]
    assert vex_failures, "expected a [vex-failure] WARN log"
    # Truncation cap is 120 chars on the *exception message*, not on the whole log line
    # so the substring "xxx...xxx" (the message portion) must be ≤ 120 'x' chars.
    for w in vex_failures:
        # Count xs after "message="
        if "message=" in w:
            tail = w.split("message=", 1)[1]
            x_run = tail.lstrip("x")
            x_count = len(tail) - len(x_run)
            assert x_count <= 120, f"expected ≤120 'x' chars after message=, got {x_count}"


# ---------------------------------------------------------------------------
# REVUE-248 — D1.b composition protocol (PositionAdapter re-validation)
# ---------------------------------------------------------------------------


def _capture_nova_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture Log.nova.warning messages dispatched during the test.

    Returns a list that the test code reads after the act phase. Uses the
    formatted % args to mirror what production logs would show.
    """
    from revue_core.comments import _verifier as verifier_mod

    captured: list[str] = []
    monkeypatch.setattr(
        verifier_mod.Log.nova,
        "warning",
        lambda msg, *args, **kwargs: captured.append(msg % args if args else msg),
    )
    return captured


def _capture_nova_infos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture Log.nova.info messages dispatched during the test."""
    from revue_core.comments import _verifier as verifier_mod

    captured: list[str] = []
    monkeypatch.setattr(
        verifier_mod.Log.nova,
        "info",
        lambda msg, *args, **kwargs: captured.append(msg % args if args else msg),
    )
    return captured


def test_correction_accepted_when_revalidation_status_is_anchored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrected line lands on a '+' line → re-validation passes → apply correction.

    ADR §D1.b: the corrected line goes through the same strict binary classifier
    that PositionAdapter uses so Vex's correction can't end up on a context or
    removed line.
    """
    # Arrange
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    captured_infos = _capture_nova_infos(monkeypatch)

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    # Diff covers lines 4 and 5 as '+' lines; corrected anchor → 5
    diff = "@@ -1,2 +1,5 @@\n line 1\n line 2\n+line 3\n+line 4\n+line 5\n"

    finding = _consolidated(
        file_path=file_rel,
        line_number=4,  # reported on blank line
        code_replacement=["fixed value"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="anchor moved one line down",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert — correction applied; success INFO logged
    assert out is not None
    assert out.line_number == 5
    assert any("[vex-correction-revalidated]" in m and "ANCHORED" in m for m in captured_infos)


def test_correction_rejected_when_revalidation_status_is_context_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrected line lands on a context (' ') line → re-validation rejects → keep reported line."""
    # Arrange
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    captured_warnings = _capture_nova_warnings(monkeypatch)

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    # Diff: line 5 is a context line, line 4 is a '+' line
    diff = "@@ -3,3 +3,4 @@\n line 3\n+line 4\n line 5\n line 6\n"

    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x = 1"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert — correction reverted, original reported line preserved
    assert out is not None
    assert out.line_number == 4
    assert any(
        "[vex-correction-rejected]" in w and "CONTEXT_LINE" in w
        for w in captured_warnings
    )


def test_correction_rejected_when_revalidation_status_is_out_of_hunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrected line outside any hunk → revert to reported line."""
    # Arrange
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    captured_warnings = _capture_nova_warnings(monkeypatch)

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    # Diff only covers line 4; corrected anchor at line 8 is outside.
    diff = "@@ -3,1 +3,2 @@\n line 3\n+line 4\n"

    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x = 1"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=8, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.line_number == 4
    assert any(
        "[vex-correction-rejected]" in w and "OUT_OF_HUNK" in w
        for w in captured_warnings
    )


def test_correction_rejected_when_diff_for_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If diff_by_file has no entry for the finding's file, fail CLOSED:
    revert to the agent's reported line + WARN with status=NO_DIFF.

    Pre-patch behaviour was fail-open (silently accept). The composition gate
    must not be bypassed by a missing-diff edge case — that defeats AC3.
    """
    # Arrange
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    captured_warnings = _capture_nova_warnings(monkeypatch)

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    finding = _consolidated(
        file_path=file_rel,
        line_number=4,
        code_replacement=["x"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=5, replacement_line_count=1),
    )
    # diff_by_file maps the *file* but with an empty/whitespace diff →
    # _diff_for returns None → composition gate must reject.
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "   \n"},  # whitespace-only → no diff
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.line_number == 4
    assert any(
        "[vex-correction-rejected]" in w and "NO_DIFF" in w for w in captured_warnings
    )


def test_correction_rejected_when_corrected_line_strictly_in_minus_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrected line falls only in minus_old (no matching plus_new) → REMOVED_LINE → revert."""
    # Arrange
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict

    captured_warnings = _capture_nova_warnings(monkeypatch)

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    # Diff: lines 5,6,7 in the old file are removed; no '+' lines around them.
    # corrected_anchor.line=6 → minus_old contains 6 → REMOVED_LINE.
    diff = "@@ -5,3 +5,0 @@\n-old 5\n-old 6\n-old 7\n"

    finding = _consolidated(
        file_path=file_rel,
        line_number=5,
        code_replacement=["x"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="moved",
        corrected_anchor=CorrectedAnchor(line=6, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.line_number == 5
    assert any(
        "[vex-correction-rejected]" in w and "REMOVED_LINE" in w
        for w in captured_warnings
    )


# ---------------------------------------------------------------------------
# REVUE-248 — D1.a hallucination-clamp window (K=10)
# ---------------------------------------------------------------------------


def test_correction_within_clamp_window_is_applied(tmp_path: Path) -> None:
    """Correction delta ≤ VEX_CORRECTION_MAX_DELTA is accepted (subject to AC3 re-validation).

    Tests the lower-bound boundary of the clamp: |corrected - reported| == K is
    allowed; only delta > K is rejected.
    """
    # Arrange
    from revue_core.comments._verifier import (
        CorrectedAnchor,
        VEX_CORRECTION_MAX_DELTA,
        VexVerdict,
    )

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    reported_line = 10
    corrected_line = reported_line + VEX_CORRECTION_MAX_DELTA  # boundary

    finding = _consolidated(
        file_path=file_rel,
        line_number=reported_line,
        code_replacement=["x = 1"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="ok",
        corrected_anchor=CorrectedAnchor(line=corrected_line, replacement_line_count=1),
    )

    # Diff covers the corrected line so re-validation passes.
    diff = f"@@ -0,0 +{reported_line},{VEX_CORRECTION_MAX_DELTA + 1} @@\n" + "".join(
        f"+line {reported_line + i}\n" for i in range(VEX_CORRECTION_MAX_DELTA + 1)
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert
    assert out is not None
    assert out.line_number == corrected_line


def test_correction_beyond_clamp_window_is_rejected_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correction delta > K is dropped — the finding keeps its reported line.

    ADR §D1.a: a hallucinated correction must not be able to place a comment
    at an arbitrary wrong line. The clamp is the upper bound on Vex's blast
    radius.
    """
    # Arrange
    from revue_core.comments import _verifier as verifier_mod
    from revue_core.comments._verifier import (
        CorrectedAnchor,
        VEX_CORRECTION_MAX_DELTA,
        VexVerdict,
    )

    captured_warnings: list[str] = []
    monkeypatch.setattr(
        verifier_mod.Log.nova,
        "warning",
        lambda msg, *args, **kwargs: captured_warnings.append(msg % args if args else msg),
    )

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    reported_line = 10
    out_of_bounds_line = reported_line + VEX_CORRECTION_MAX_DELTA + 1

    finding = _consolidated(
        file_path=file_rel,
        line_number=reported_line,
        code_replacement=["x = 1"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="ok",
        corrected_anchor=CorrectedAnchor(line=out_of_bounds_line, replacement_line_count=1),
    )
    # Diff covers only the original reported line; Vex's hallucinated line is
    # arbitrarily far away. The clamp should reject before re-validation runs.
    diff = "@@ -0,0 +10,1 @@\n+line 10\n"
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert — finding posts at the original line, not at the hallucination
    assert out is not None
    assert out.line_number == reported_line
    # WARN log fired with the documented prefix
    assert any(
        "[vex-anchor-out-of-bounds]" in w and "window_exceeded" in w
        for w in captured_warnings
    )


def test_correction_below_clamp_window_is_rejected(tmp_path: Path) -> None:
    """Clamp is symmetric: corrections delta below -K are also rejected."""
    # Arrange
    from revue_core.comments._verifier import (
        CorrectedAnchor,
        VEX_CORRECTION_MAX_DELTA,
        VexVerdict,
    )

    file_rel = "src/example.py"
    (tmp_path / file_rel).parent.mkdir(parents=True)
    (tmp_path / file_rel).write_text("placeholder\n")

    reported_line = 50
    out_of_bounds_line = reported_line - VEX_CORRECTION_MAX_DELTA - 1

    finding = _consolidated(
        file_path=file_rel,
        line_number=reported_line,
        code_replacement=["x = 1"],
        replacement_line_count=1,
    )
    verifier = MagicMock()
    verifier.verify.return_value = VexVerdict(
        verdict="apply",
        reason="ok",
        corrected_anchor=CorrectedAnchor(line=out_of_bounds_line, replacement_line_count=1),
    )
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: "@@ -0,0 +50,1 @@\n+line 50\n"},
    )

    # Act
    out = pp.process(finding)

    # Assert — finding posts at original line; correction discarded
    assert out is not None
    assert out.line_number == reported_line


def test_drop_cr_keep_prose_with_corrected_anchor_repositions_prose_to_correct_line(
    tmp_path: Path,
) -> None:
    """Vex rejects the patch and tells us where the prose should anchor instead."""
    # Arrange
    from revue_core.comments._verifier import CorrectedAnchor, VexVerdict
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
    # Diff covers both reported (86) and corrected (91) so re-validation accepts.
    diff = "@@ -0,0 +86,10 @@\n" + "".join(f"+line {86 + i}\n" for i in range(10))
    pp = VexVerifyPostProcessor(
        verifier=verifier,
        repo_root=tmp_path,
        diff_by_file={file_rel: diff},
    )

    # Act
    out = pp.process(finding)

    # Assert — prose repositioned, suggestion dropped
    assert out is not None
    assert out.line_number == 91
    assert out.code_replacement is None
    assert out.replacement_line_count == 1

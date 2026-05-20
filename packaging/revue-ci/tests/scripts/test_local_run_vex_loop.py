"""Slice 3 — Vex in-loop via split Phase 3 in /revue-local.

Validates that ``scripts/local_run.py``'s new ``classify-and-build-vex-jobs``
(Phase 3a) and ``apply-verdicts-and-finalize`` (Phase 3c) subcommands run the
production Vex prompt builder + verdict applicator + OrphanLineGuard backstop
at zero Anthropic API cost. The LLM call is externalised to orchestrator
Agent forks (Phase 3b, skill-side) — the Python subcommands handle prompt
construction (3a) and verdict application + orphan-guard sweep (3c).

Tests are written against helpers extracted into ``scripts/local_run.py``;
the module is loaded by absolute path because ``scripts/`` is not a package.

Critical guard: ``test_local_run_vex_prompt_matches_production`` enforces
byte-equivalence between the local-skill Vex prompt and a fresh production
``VexVerifier._build_prompt(...)`` invocation. Without this, the local Vex
output is not comparable to production and the whole exercise is pointless.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
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


@pytest.fixture
def revue_logger_to_stdout():
    """F1 — install a stdout proxy hook on the ``RevueLogger`` singleton.

    ``Log.cli.warning`` / ``Log.cli.error`` dispatch through
    ``RevueLogger.shared()._proxy_hook``, which is ``None`` in a clean pytest
    run because the install at ``src/revue/cli.py:45`` only runs at CLI entry.
    Without this fixture, capsys captures nothing the production code emitted
    and the assertion only passes when a prior test leaked a hook into the
    process-wide singleton (order-dependent flake).

    The fixture installs a stdout writer for the test's duration and
    restores the prior hook (typically ``None``) on teardown.
    """
    from revue_core.core.log import RevueLogger
    shared = RevueLogger.shared()
    saved_hook = shared._proxy_hook

    def _to_stdout(message: str) -> None:
        sys.stdout.write(message + "\n")
        sys.stdout.flush()

    shared.setup(on_log=_to_stdout)
    try:
        yield
    finally:
        shared._proxy_hook = saved_hook


# ---------------------------------------------------------------------------
# Helpers — synthetic ConsolidatedFinding builders
# ---------------------------------------------------------------------------


_DEFAULT_CODE_REPLACEMENT: list[str] = ["    return value + 1"]


def _make_finding(
    *,
    file_path: str = "src/example.py",
    line_number: int = 10,
    code_replacement: "list[str] | None" = _DEFAULT_CODE_REPLACEMENT,
    replacement_line_count: int = 1,
    issue: str = "Off-by-one in increment.",
    suggestion: str = "Use value + 1 instead of value.",
):
    """Build a ConsolidatedFinding with sensible defaults.

    ``code_replacement=None`` is preserved as the explicit "prose-only" signal
    (no Vex job, mirrors ``VexVerifyPostProcessor`` early-return). The default
    is a real list to match the type annotation (P8).
    """
    from revue_core.comments.models import Attribution, ConsolidatedFinding

    return ConsolidatedFinding(
        file_path=file_path,
        line_number=line_number,
        severity="medium",
        issue=issue,
        suggestion=suggestion,
        confidence=0.8,
        category="bug",
        attribution=[Attribution(agent_name="maya", category="bug")],
        code_replacement=list(code_replacement) if code_replacement is not None else None,
        replacement_line_count=replacement_line_count,
        snippet="",
    )


# ---------------------------------------------------------------------------
# AC4 — byte-equivalence with the production prompt builder
# ---------------------------------------------------------------------------


def test_local_run_vex_prompt_matches_production(local_run, tmp_path):
    """The Vex job's system + user prompt must be byte-identical to the canonical
    production prompt.

    What this test covers:
      - The local-skill ``system_prompt`` equals the canonical
        ``_DEFAULT_SYSTEM_PROMPT`` constant imported from ``_verifier``
        (catches a future construction-time override on the local path; P5).
      - The local-skill ``user_prompt`` equals the byte-identical output of
        ``VexVerifier._build_prompt(...)`` on the same inputs.

    What this test does NOT cover:
      - A pipeline that wires ``VexVerifier(system_prompt=...)`` with a
        non-default prompt (production may do this via .yaml in the future).
        That contract belongs to the pipeline tests.

    If either path drifts, every other Slice-3 test is meaningless — the
    local skill stops being comparable to production. This is the critical
    drift guard called out in the plan.
    """
    from revue_core.comments._verifier import VexVerifier, _DEFAULT_SYSTEM_PROMPT

    finding = _make_finding(
        file_path="src/example.py",
        line_number=12,
        code_replacement=["    return value + 1"],
        replacement_line_count=1,
    )
    file_content = (
        "def f(value):\n"
        "    # context line\n"
        "    return value\n"
    )

    expected_user = VexVerifier._build_prompt(
        file_content=file_content,
        finding=finding,
    )

    # Local-skill path — call the helper that 3a uses to build job files.
    system_prompt, user_prompt = local_run._build_vex_prompts(
        file_content=file_content,
        finding=finding,
    )

    # P5: compare LOCAL system prompt against the canonical module constant,
    # not against a fresh VexVerifier instance attribute (which the local
    # helper also reads — a tautology).
    assert system_prompt == _DEFAULT_SYSTEM_PROMPT
    assert user_prompt == expected_user


# ---------------------------------------------------------------------------
# AC1 — Phase 3a emits one Vex job per code_replacement finding
# ---------------------------------------------------------------------------


def test_local_run_emits_vex_job_per_code_replacement_finding(local_run, tmp_path):
    """N findings with non-None code_replacement → N job entries in the manifest.

    Files referenced by findings are written into a synthetic repo root so
    the job builder can fetch their content for the prompt.
    """
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("def a():\n    return 1\n")
    (repo_root / "b.py").write_text("def b():\n    return 2\n")

    findings = [
        _make_finding(file_path="a.py", line_number=2, code_replacement=["    return 1 + 1"]),
        _make_finding(file_path="b.py", line_number=2, code_replacement=["    return 2 + 1"]),
    ]

    manifest = local_run._build_vex_job_manifest(
        findings=findings,
        repo_root=repo_root,
        jobs_dir=jobs_dir,
        max_vex_forks=20,
    )

    assert len(manifest["jobs"]) == 2
    for entry in manifest["jobs"]:
        job_file = Path(entry["job_file"])
        assert job_file.exists()
        data = json.loads(job_file.read_text())
        assert "system_prompt" in data and "user_prompt" in data
        assert data["finding_index"] == entry["finding_index"]
        assert data["output_file_path"] == entry["output_file"]


def test_local_run_skips_vex_for_prose_only_findings(local_run, tmp_path):
    """Findings with code_replacement=None must NOT emit a Vex job."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("def a():\n    return 1\n")

    findings = [
        _make_finding(file_path="a.py", code_replacement=None),
        _make_finding(file_path="a.py", line_number=1, code_replacement=["new line"]),
    ]

    manifest = local_run._build_vex_job_manifest(
        findings=findings,
        repo_root=repo_root,
        jobs_dir=jobs_dir,
        max_vex_forks=20,
    )

    # Only the second finding should have a Vex job.
    assert len(manifest["jobs"]) == 1
    assert manifest["jobs"][0]["finding_index"] == 1


# ---------------------------------------------------------------------------
# AC2 — Phase 3c applies verdicts via the production _apply_verdict
# ---------------------------------------------------------------------------


def test_local_run_applies_drop_cr_verdict_correctly(local_run, tmp_path):
    """A ``drop_cr_keep_prose`` verdict strips code_replacement, keeps prose."""
    finding = _make_finding(
        line_number=10,
        code_replacement=["    new line"],
        replacement_line_count=1,
        issue="oops",
        suggestion="fix it",
    )
    verdict_payload = {
        "verdict": "drop_cr_keep_prose",
        "reason": "Replacement breaks indent.",
        "corrected_anchor": None,
    }

    result = local_run._apply_vex_verdict_to_finding(finding, verdict_payload)

    assert result is not None
    assert result.code_replacement is None
    assert result.suggestion == "fix it"
    assert result.issue == "oops"
    assert result.replacement_line_count == 1


def test_local_run_applies_corrected_anchor(local_run, tmp_path):
    """An ``apply`` verdict with ``corrected_anchor`` mutates line + rlc."""
    finding = _make_finding(
        line_number=10,
        code_replacement=["    new line one", "    new line two"],
        replacement_line_count=2,
    )
    verdict_payload = {
        "verdict": "apply",
        "reason": "Patch is safe but anchored one line low.",
        "corrected_anchor": {"line": 11, "replacement_line_count": 3},
    }

    result = local_run._apply_vex_verdict_to_finding(finding, verdict_payload)

    assert result is not None
    assert result.line_number == 11
    assert result.replacement_line_count == 3
    # Replacement content preserved on apply.
    assert result.code_replacement == ["    new line one", "    new line two"]


def test_local_run_clamps_corrected_anchor_delta(local_run, tmp_path):
    """P13 — when Vex emits a corrected_anchor whose delta from the agent's
    reported line exceeds ``VEX_CORRECTION_MAX_DELTA`` (K=10), the local path
    must clamp/reject the correction rather than blindly apply it.

    Mirrors production ``_sanitize_correction`` Gate 1 (clamp). Without this,
    a hallucinated line can shift the comment arbitrarily far.
    """
    from revue_core.comments._verifier import VEX_CORRECTION_MAX_DELTA

    finding = _make_finding(
        line_number=10,
        code_replacement=["    new line"],
        replacement_line_count=1,
    )
    # delta = K+5 → must be clamped/rejected.
    bad_line = finding.line_number + VEX_CORRECTION_MAX_DELTA + 5
    verdict_payload = {
        "verdict": "apply",
        "reason": "Patch is safe (hallucinated anchor).",
        "corrected_anchor": {"line": bad_line, "replacement_line_count": 1},
    }

    result = local_run._apply_vex_verdict_to_finding(finding, verdict_payload)

    assert result is not None
    # The corrected line was outside the K-window → must revert to the
    # agent's reported line (production semantics: corrected_anchor → None,
    # so ``apply`` mutates nothing).
    assert result.line_number == finding.line_number


def test_local_run_clamps_replacement_line_count_to_min_1(local_run, tmp_path):
    """P13 / F4 — a corrected_anchor with replacement_line_count < 1 must be
    clamped to 1 inside ``_coerce_verdict`` BEFORE ``CorrectedAnchor`` is
    constructed, so the malformed rlc doesn't get silently swallowed by the
    dataclass invariant and the anchor still applies (line + rlc=1) rather
    than reverting to the agent's reported line.
    """
    finding = _make_finding(line_number=10, code_replacement=["    new"], replacement_line_count=1)
    verdict_payload = {
        "verdict": "apply",
        "reason": "ok",
        "corrected_anchor": {"line": 11, "replacement_line_count": 0},
    }

    result = local_run._apply_vex_verdict_to_finding(finding, verdict_payload)

    assert result is not None
    # Clamp was applied: anchor is NOT discarded, so the corrected line
    # is used (11, not the original 10) and rlc is clamped to 1.
    assert result.line_number == 11
    assert result.replacement_line_count == 1


# ---------------------------------------------------------------------------
# AC2 — chain order: Vex first, then OrphanLineGuard
# ---------------------------------------------------------------------------


def test_local_run_runs_orphan_guard_after_vex(local_run, tmp_path):
    """OrphanLineGuard runs *after* verdicts are applied.

    Construct a 2-line replacement that Vex would ``apply`` (no anchor
    correction), but whose proposed range under-reaches the file's block
    terminator. OrphanLineGuard must then downgrade it to prose-only.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # File where the loop continues past the proposed replacement range.
    # Range covers lines 1-2; line 3 is at deeper indent → block continues.
    (repo_root / "f.py").write_text(
        "def f():\n"
        "    total = 0\n"
        "    for i in range(10):\n"
        "        total += i\n"
        "    return total\n"
    )
    finding = _make_finding(
        file_path="f.py",
        line_number=2,
        code_replacement=["    total = 0", "    for i in range(5):"],
        replacement_line_count=2,
    )
    diff_by_file = {"f.py": "diff f"}
    verdicts = {0: {"verdict": "apply", "reason": "ok", "corrected_anchor": None}}

    finalised = local_run._apply_verdicts_and_finalise(
        findings=[finding],
        verdicts_by_index=verdicts,
        diff_by_file=diff_by_file,
        repo_root=repo_root,
    )

    assert len(finalised) == 1
    # OrphanLineGuard should have stripped code_replacement (block continues).
    assert finalised[0].code_replacement is None
    assert finalised[0].replacement_line_count == 1


# ---------------------------------------------------------------------------
# F6 — local chain iterates the canonical post-processor chain
# ---------------------------------------------------------------------------


def test_local_chain_iterates_canonical_postprocessors(local_run, tmp_path, monkeypatch):
    """F6 — ``_apply_verdicts_and_finalise`` iterates the chain returned by
    ``build_consolidation_postprocessors`` rather than hard-coding stages.

    Asserted as follows:
      1. With the real canonical chain, the call completes (no drift today).
      2. When a synthetic unknown stage is injected into the chain returned
         by the helper, the local path raises ``NotImplementedError`` —
         this is the drift-detection guard. Without it, a future addition
         to the canonical chain would silently be skipped locally.
    """
    from revue_core.core import pipeline as pipeline_mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "f.py").write_text("x = 0\n")

    finding = _make_finding(
        file_path="f.py", line_number=1,
        code_replacement=["x = 1"], replacement_line_count=1,
    )

    # (1) Baseline — real chain, no drift, completes cleanly.
    result = local_run._apply_verdicts_and_finalise(
        findings=[finding],
        verdicts_by_index={},
        diff_by_file={"f.py": ""},
        repo_root=repo_root,
    )
    assert len(result) == 1

    # (2) Inject a synthetic unknown stage into the canonical chain — the
    # local path must surface this as NotImplementedError.
    class _UnknownStage:
        def process(self, finding):
            return finding

    real_builder = pipeline_mod.build_consolidation_postprocessors

    def _drifted_builder(**kwargs):
        chain = real_builder(**kwargs)
        return chain + [_UnknownStage()]

    monkeypatch.setattr(
        pipeline_mod, "build_consolidation_postprocessors", _drifted_builder,
    )

    with pytest.raises(NotImplementedError, match="_UnknownStage"):
        local_run._apply_verdicts_and_finalise(
            findings=[finding],
            verdicts_by_index={},
            diff_by_file={"f.py": ""},
            repo_root=repo_root,
        )


# ---------------------------------------------------------------------------
# AC5 — concurrency cap
# ---------------------------------------------------------------------------


def test_local_run_respects_max_vex_forks_cap(local_run, tmp_path, capsys):
    """When findings-needing-Vex > --max-vex-forks, the 21st (and beyond)
    is skipped and a warning is emitted on stderr.

    Skipped findings pass through with their code_replacement intact (no
    verdict applied, no orphan-guard sweep — both depend on Vex's decision).

    Uses pytest's ``capsys`` rather than swapping ``sys.stderr`` directly so
    coverage survives a future switch from ``print(file=sys.stderr)`` to
    logging (P9).
    """
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("x = 0\n" * 30)
    findings = [
        _make_finding(
            file_path="a.py",
            line_number=i + 1,
            code_replacement=[f"x = {i}"],
        )
        for i in range(21)
    ]

    manifest = local_run._build_vex_job_manifest(
        findings=findings,
        repo_root=repo_root,
        jobs_dir=jobs_dir,
        max_vex_forks=20,
    )

    err_text = capsys.readouterr().err

    assert len(manifest["jobs"]) == 20
    assert "max_vex_forks=20" in err_text
    # Whole-token assertion of skipped count (P4): match "1 finding(s)" exactly
    # so the substring "1" can't accidentally satisfy by matching "20".
    assert re.search(r"\b1 finding\(s\)", err_text)


# ---------------------------------------------------------------------------
# P2 — reject --max-vex-forks=0 and negative values
# ---------------------------------------------------------------------------


def test_local_run_rejects_zero_max_vex_forks(local_run, tmp_path):
    """``--max-vex-forks 0`` must return exit code 2 rather than silently emit
    zero jobs (which would let the slice cap-bypass without surfacing).
    """
    import argparse

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    args = argparse.Namespace(
        jobs_dir=str(jobs_dir), nova_output=None, max_vex_forks=0,
    )
    rc = local_run.cmd_classify_and_build_vex_jobs(args)
    assert rc == 2


def test_local_run_rejects_negative_max_vex_forks(local_run, tmp_path):
    """``--max-vex-forks -1`` is silently surprising (drops last finding via
    list-slice semantics); reject at the boundary."""
    import argparse

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    args = argparse.Namespace(
        jobs_dir=str(jobs_dir), nova_output=None, max_vex_forks=-1,
    )
    rc = local_run.cmd_classify_and_build_vex_jobs(args)
    assert rc == 2


# ---------------------------------------------------------------------------
# P3 — missing diff_by_file.json in 3a/3c surfaces a clear error
# ---------------------------------------------------------------------------


def test_apply_verdicts_missing_diff_by_file_fails_clearly(
    local_run, tmp_path, capsys, revue_logger_to_stdout,
):
    """3c must NOT crash with FileNotFoundError when ``diff_by_file.json`` is
    missing — emit a clear error and return non-zero (P3).

    The ``revue_logger_to_stdout`` fixture is included for parity with the
    cap-warning test; ``Log.cli.error`` would otherwise dispatch through a
    ``None`` proxy hook in a clean pytest run (F1).
    """
    import argparse

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    # Snapshot exists (so 3c proceeds past its first guard) but
    # diff_by_file.json does not.
    (jobs_dir / "consolidated_findings_snapshot.json").write_text("[]")

    args = argparse.Namespace(jobs_dir=str(jobs_dir), platform="github")
    rc = local_run.cmd_apply_verdicts_and_finalize(args)
    assert rc != 0


# ---------------------------------------------------------------------------
# P11 — cap-overflow surfaced in 3c output
# ---------------------------------------------------------------------------


def test_apply_verdicts_warns_when_cap_fired_in_3a(
    local_run, tmp_path, capsys, revue_logger_to_stdout,
):
    """When the 3a manifest reports skipped indices, 3c must surface a
    warning so users reading only final output know the cap fired (P11).

    Revue's logger writes through a custom channel (``Log.cli``), not the
    stdlib root logger, so we assert on captured stdout rather than caplog.
    """
    import argparse

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "consolidated_findings_snapshot.json").write_text("[]")
    (jobs_dir / "diff_by_file.json").write_text("{}")
    vex_dir = jobs_dir / "vex_jobs"
    vex_dir.mkdir()
    (vex_dir / "manifest.json").write_text(json.dumps({
        "jobs": [],
        "skipped_indices": [5, 6, 7],
    }))

    args = argparse.Namespace(jobs_dir=str(jobs_dir), platform="github")
    rc = local_run.cmd_apply_verdicts_and_finalize(args)
    assert rc == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert re.search(r"3 finding\(s\) bypassed Vex", combined), (
        f"expected cap warning in output: {combined!r}"
    )

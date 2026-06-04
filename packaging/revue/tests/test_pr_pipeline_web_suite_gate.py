"""REVUE-393 — the FastAPI web suite (src/web/tests) must be a hard CI gate on PRs.

The root ``tests/`` CI suite runs against ``requirements-ci.txt``, which omits
the web-app deps (fastapi/uvicorn). Before this ticket nothing in CI ran
``src/web/tests/`` — a customer-facing surface (licence activation, validation,
billing/webhooks, manifest) had zero CI gating. These tests codify that the
pull-request pipeline now installs the web deps into an isolated venv and runs
the non-e2e web suite as a blocking step, so the gap cannot silently re-open.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
PIPELINES_FILE = REPO_ROOT / "bitbucket-pipelines.yml"


def _load_pipeline() -> dict[str, Any]:
    return yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))


def _flatten_steps(entries: list[Any]) -> list[dict[str, Any]]:
    """Flatten a pipeline entry list, unwrapping ``parallel`` blocks (list form
    or ``{steps: [...]}`` form) into their constituent steps, preserving order."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if "step" in entry:
            out.append(entry["step"])
        elif "parallel" in entry:
            par = entry["parallel"]
            items = par.get("steps", []) if isinstance(par, dict) else par
            out.extend(s["step"] for s in items if isinstance(s, dict) and "step" in s)
    return out


def _parallel_groups(entries: list[Any]) -> list[list[str]]:
    """Names of the steps inside each ``parallel`` block (one inner list per block)."""
    groups: list[list[str]] = []
    for entry in entries:
        if isinstance(entry, dict) and "parallel" in entry:
            par = entry["parallel"]
            items = par.get("steps", []) if isinstance(par, dict) else par
            groups.append(
                [s["step"].get("name", "") for s in items if isinstance(s, dict) and "step" in s]
            )
    return groups


def _pr_steps() -> list[dict[str, Any]]:
    """The flattened step list for the catch-all ``pull-requests`` pipeline."""
    pipeline = _load_pipeline()
    return _flatten_steps(pipeline["pipelines"]["pull-requests"]["**"])


def _pr_step_names() -> list[str]:
    return [step.get("name", "") for step in _pr_steps()]


def _web_step() -> dict[str, Any]:
    for step in _pr_steps():
        if "web" in step.get("name", "").lower():
            return step
    raise AssertionError(
        f"pull-requests pipeline has no web-test step. Steps were: {_pr_step_names()}"
    )


def _script_text(step: dict[str, Any]) -> str:
    return "\n".join(str(line) for line in step.get("script", []))


def test_pr_pipeline_has_a_web_test_step() -> None:
    """AC1: a web-suite step exists in the pull-requests pipeline."""
    # Act
    names = _pr_step_names()

    # Assert — a web-named step is present on every PR
    assert any("web" in n.lower() for n in names), (
        f"pull-requests pipeline must run the web suite; steps={names}"
    )


def test_web_step_runs_non_e2e_web_suite() -> None:
    """AC1/AC3: the step runs src/web/tests with e2e excluded, plus the
    relocated rate-limit suite — and does NOT skip via importorskip."""
    # Arrange
    script = _script_text(_web_step())

    # Assert — the non-e2e web suite is exercised
    assert "src/web/tests/" in script, "web step must run the src/web/tests suite"
    assert "--ignore=src/web/tests/e2e" in script, (
        "web step must exclude the e2e subset (REVUE-332 uvicorn loop leak)"
    )
    # Assert — the rate-limit suite that lives in root tests/ is run here, where
    # fastapi IS installed, so its 16 tests execute for real.
    assert "tests/test_activation_rate_limit.py" in script, (
        "web step must run the relocated activation rate-limit suite"
    )


def test_web_step_installs_isolated_web_venv() -> None:
    """AC4: web deps install into a dedicated venv so the lighter packaging/root
    steps are not bloated or slowed."""
    # Arrange
    step = _web_step()
    script = _script_text(step)

    # Assert — deps come from the web requirements file
    assert "src/web/requirements.txt" in script, (
        "web step must install from src/web/requirements.txt"
    )
    # Assert — into an isolated venv, NOT the shared .venv / ci-venv cache
    assert ".venv-web" in script, "web step must use an isolated .venv-web"
    cache_entries = step.get("caches", [])
    assert "ci-venv" not in cache_entries, (
        "web step must not share the ci-venv cache with the lighter root step"
    )
    assert "web-venv" in cache_entries, "web step should cache its isolated web venv"


def test_web_step_is_a_hard_gate_not_advisory() -> None:
    """AC2: a web-test failure fails the pipeline (no allow-failure escape hatch)."""
    # Arrange
    step = _web_step()

    # Assert — a plain step fails the pipeline by default; assert no opt-out flag
    # was added that would downgrade it to advisory.
    assert step.get("allow-failure", False) is False, (
        "web step must be a hard gate — allow-failure must not be set"
    )


def test_web_step_runs_before_ai_review() -> None:
    """The web gate runs in the existing test phase, before the AI review step,
    matching the &run-tests → &revue-review ordering."""
    # Arrange
    names = _pr_step_names()
    web_idx = next(i for i, n in enumerate(names) if "web" in n.lower())
    review_idx = next(
        (i for i, n in enumerate(names) if "review" in n.lower()), None
    )

    # Assert — web gate precedes the AI review when both are present
    if review_idx is not None:
        assert web_idx < review_idx, (
            f"web test gate must run before the AI review step; names={names}"
        )


def _named_steps(pipeline_entry: list[Any]) -> list[str]:
    """Names of all steps, flattening ``parallel`` blocks (so a web step nested
    inside a parallel block is still seen)."""
    return [s.get("name", "") for s in _flatten_steps(pipeline_entry)]


def _has_web(steps: list[str]) -> bool:
    return any("web" in n.lower() and "test" in n.lower() for n in steps)


def test_web_gate_present_in_develop_main_and_tag_pipelines() -> None:
    """REVUE-393 (extended): a web regression must not reach prod or PyPI ungated.

    The PR pipeline alone is insufficient — develop merges, main deploys to
    prod, and tag builds publish to PyPI. Each runs the light ``*run-tests``
    step, which can't exercise the web app, so each must also run the web gate.
    """
    pipelines = _load_pipeline()["pipelines"]
    assert _has_web(_named_steps(pipelines["branches"]["develop"])), "develop must gate the web suite"
    assert _has_web(_named_steps(pipelines["branches"]["main"])), "main must gate the web suite"
    assert _has_web(_named_steps(pipelines["tags"]["v*"])), "tag builds must gate the web suite"


def test_unit_and_web_suites_run_in_one_parallel_block() -> None:
    """REVUE-393: unit (Run Tests) and web (Run Web Tests) are independent
    (separate venvs/deps/code) and run in a single parallel block, so CI
    wall-clock is max(unit, web) not unit+web. The AI review still runs after,
    only on a green parallel block (fail-fast before spending AI tokens)."""
    pipelines = _load_pipeline()["pipelines"]
    for label, entries in (
        ("pull-requests", pipelines["pull-requests"]["**"]),
        ("develop", pipelines["branches"]["develop"]),
        ("main", pipelines["branches"]["main"]),
        ("tags", pipelines["tags"]["v*"]),
    ):
        groups = _parallel_groups(entries)
        assert any(
            any(n == "Run Tests" for n in g)
            and any("web" in n.lower() and "test" in n.lower() for n in g)
            for g in groups
        ), f"{label}: Run Tests + Run Web Tests must share one parallel block; groups={groups}"

    # On PRs the AI review must come AFTER the parallel test block.
    pr_names = _named_steps(pipelines["pull-requests"]["**"])
    web_i = next(i for i, n in enumerate(pr_names) if "web" in n.lower() and "test" in n.lower())
    rev_i = next((i for i, n in enumerate(pr_names) if "review" in n.lower()), None)
    assert rev_i is not None and web_i < rev_i, (
        f"AI review must run after the parallel test block; names={pr_names}"
    )


def test_web_gate_runs_before_deploy_and_publish() -> None:
    """The gate must precede the web-image build/deploy (main) and the PyPI
    publish chain (tags) — otherwise it cannot block a bad release."""
    pipelines = _load_pipeline()["pipelines"]

    main_names = _named_steps(pipelines["branches"]["main"])
    web_i = next(i for i, n in enumerate(main_names) if "web" in n.lower() and "test" in n.lower())
    gate_i = next(
        (i for i, n in enumerate(main_names) if "deploy" in n.lower() or "build" in n.lower()), None
    )
    assert gate_i is not None and web_i < gate_i, (
        f"web gate must run before build/deploy on main; names={main_names}"
    )

    tag_names = _named_steps(pipelines["tags"]["v*"])
    web_t = next((i for i, n in enumerate(tag_names) if "web" in n.lower() and "test" in n.lower()), None)
    pub_t = next((i for i, n in enumerate(tag_names) if "publish" in n.lower()), None)
    assert web_t is not None, f"tag pipeline must run the web suite; names={tag_names}"
    if pub_t is not None:
        assert web_t < pub_t, (
            f"web gate must run before the PyPI publish chain on tags; names={tag_names}"
        )

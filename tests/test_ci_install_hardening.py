"""Test: CI editable installs never depend on a per-run PEP 517 build-isolation fetch.

REVUE-405 — Pipelines #1026-#1028 failed on `main` at the "Run Tests" step with:

    ERROR: Could not find a version that satisfies the requirement
    hatchling>=1.21 (from versions: none)

Root cause: on a warm ci-venv cache the `requirements-ci.txt` install guard is
skipped, so `pip install -e packaging/...` is the only PyPI call. The editable
install triggers PEP 517 build isolation, which spins up an ephemeral env and
fetches `hatchling>=1.21` from the network on EVERY run. When that fetch fails,
the pipeline bricks even though nothing in the diff is wrong.

The fix removes the per-run network dependency:
  1. `requirements-ci.txt` pins the build backend (`hatchling`) and the PEP 660
     editable-build helper (`editables`) so they live in the cached venv.
  2. The test/e2e editable-install steps pass `--no-build-isolation`, so pip
     reuses the venv's hatchling instead of fetching a fresh isolated copy.

These tests are a regression guard: they fail if anyone re-introduces the
network fetch by dropping the pins or the `--no-build-isolation` flag.

The Nuitka build steps (`Build ... revue-ci`) are intentionally OUT OF SCOPE —
they use separate, single-purpose build venvs on the HIGH publish path and are
not the failing step.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINES_FILE = REPO_ROOT / "bitbucket-pipelines.yml"
REQUIREMENTS_CI = REPO_ROOT / "requirements-ci.txt"

# Steps whose editable install runs in a TEST/E2E venv (ci-venv or e2e-venv) and
# therefore must not depend on a per-run build-isolation fetch. Matched by the
# step `name:` field in bitbucket-pipelines.yml.
HARDENED_STEP_NAMES = {
    "Run Tests",
    "Revue AI Code Review",
    "Run Activate E2E",
}


def _load_pipeline() -> dict:
    return yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))


def _iter_defined_steps(pipeline: dict):
    """Yield each step dict declared under definitions.steps."""
    for entry in pipeline.get("definitions", {}).get("steps", []):
        step = entry.get("step")
        if step:
            yield step


def _editable_install_lines(step: dict) -> list[str]:
    """Script lines in `step` that run an editable install of a packaging/ pkg."""
    return [
        line
        for line in step.get("script", [])
        if isinstance(line, str)
        and "pip install" in line
        and "-e packaging/" in line
    ]


def test_pipeline_and_requirements_files_exist() -> None:
    assert PIPELINES_FILE.is_file(), f"missing: {PIPELINES_FILE}"
    assert REQUIREMENTS_CI.is_file(), f"missing: {REQUIREMENTS_CI}"


def test_requirements_ci_pins_hatchling() -> None:
    """The build backend must be installed into the cached venv, not fetched
    per-run by build isolation."""
    text = REQUIREMENTS_CI.read_text(encoding="utf-8")
    assert re.search(r"^\s*hatchling\s*>=\s*1\.21", text, re.MULTILINE), (
        "requirements-ci.txt must pin hatchling>=1.21 so the build backend is "
        "present in the cached .venv (no per-run build-isolation fetch)"
    )


def test_requirements_ci_pins_editables() -> None:
    """PEP 660 editable builds need `editables`; pin it so --no-build-isolation
    has the full build-dep closure available."""
    text = REQUIREMENTS_CI.read_text(encoding="utf-8")
    assert re.search(r"^\s*editables\b", text, re.MULTILINE), (
        "requirements-ci.txt must pin `editables` (PEP 660 editable-build helper) "
        "so --no-build-isolation can build the editable wheels offline"
    )


def test_test_and_e2e_editable_installs_disable_build_isolation() -> None:
    """Every editable install in a test/e2e step must pass --no-build-isolation."""
    pipeline = _load_pipeline()
    checked: set[str] = set()

    for step in _iter_defined_steps(pipeline):
        name = step.get("name", "")
        if name not in HARDENED_STEP_NAMES:
            continue
        checked.add(name)
        install_lines = _editable_install_lines(step)
        assert install_lines, (
            f"step {name!r} was expected to run an editable install but none was "
            f"found — update HARDENED_STEP_NAMES if the pipeline changed"
        )
        for line in install_lines:
            assert "--no-build-isolation" in line, (
                f"step {name!r} editable install must use --no-build-isolation to "
                f"avoid a per-run build-isolation PyPI fetch of hatchling; got: {line!r}"
            )

    missing = HARDENED_STEP_NAMES - checked
    assert not missing, f"expected test/e2e steps not found in pipeline: {missing}"


def test_activate_e2e_makes_build_backend_available_in_its_venv() -> None:
    """`Run Activate E2E` uses .venv-e2e (keyed on pyproject, NOT requirements-ci.txt),
    so it must install hatchling+editables explicitly before its editable install —
    otherwise --no-build-isolation has nothing to build with."""
    pipeline = _load_pipeline()
    for step in _iter_defined_steps(pipeline):
        if step.get("name") != "Run Activate E2E":
            continue
        script = "\n".join(
            line for line in step.get("script", []) if isinstance(line, str)
        )
        assert "hatchling" in script and "editables" in script, (
            "Run Activate E2E must install hatchling+editables into .venv-e2e "
            "before the --no-build-isolation editable install (its cache key does "
            "not include requirements-ci.txt)"
        )
        return
    raise AssertionError("Run Activate E2E step not found in pipeline")

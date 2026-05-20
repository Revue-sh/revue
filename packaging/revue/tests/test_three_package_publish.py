"""REVUE-310 — Bitbucket Pipelines must wire up the 3-package atomic publish.

revue_core → revue-ci → revue must:
- be built in order on tag pipelines
- publish in order (revue_core first, both dependents last)
- fail-fast if revue_core publish fails

We can't run the publish in tests; we verify the YAML declares each step in
the right relative position. Names are matched substring-wise so trivial
renames don't break the test (e.g. "Build macOS ARM64 — revue-ci" still
matches "revue-ci").
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
PIPELINES_FILE = REPO_ROOT / "bitbucket-pipelines.yml"


def _tag_step_names() -> list[str]:
    doc = yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))
    return [s.get("step", {}).get("name", "") for s in doc["pipelines"]["tags"]["v*"]]


def _first_index_matching(names: list[str], substr: str) -> int:
    for i, n in enumerate(names):
        if substr.lower() in n.lower():
            return i
    pytest.fail(
        f"tag pipeline has no step matching '{substr}'. Steps were: {names}"
    )


def test_three_packages_build_in_dependency_order() -> None:
    """revue_core builds before revue-ci, which builds before the skill.

    Step names follow `Build <platform> — <pkg>` so we match on the package
    name suffix rather than the full leading substring.
    """
    names = _tag_step_names()
    i_core = _first_index_matching(names, "revue_core")
    i_ci = _first_index_matching(names, "revue-ci")
    i_skill = _first_index_matching(names, "revue skill")
    assert i_core < i_ci < i_skill, (
        f"build order must be revue_core → revue-ci → revue skill; "
        f"got positions core={i_core}, revue-ci={i_ci}, skill={i_skill}"
    )


def test_three_packages_publish_in_dependency_order() -> None:
    """Publish chain: revue_core → revue-ci → skill. Fail-fast by sequencing."""
    names = _tag_step_names()
    i_pub_core = _first_index_matching(names, "Publish revue_core")
    i_pub_ci = _first_index_matching(names, "Publish revue-ci")
    i_pub_skill = _first_index_matching(names, "Publish revue skill")
    assert i_pub_core < i_pub_ci < i_pub_skill, (
        f"publish order must be revue_core → revue-ci → revue skill; "
        f"got positions core={i_pub_core}, revue-ci={i_pub_ci}, skill={i_pub_skill}"
    )


def test_all_three_packages_have_a_publish_step() -> None:
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    for needed in (
        "Publish revue_core",
        "Publish revue-ci",
        "Publish revue skill",
    ):
        assert needed in content, (
            f"bitbucket-pipelines.yml is missing the '{needed}' step"
        )


def test_publish_steps_use_twine_and_pypi_token() -> None:
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    assert content.count("PYPI_API_TOKEN") >= 3, (
        "expected PYPI_API_TOKEN in each of the three publish steps"
    )
    assert content.count("twine upload") >= 3, (
        "expected `twine upload` in each of the three publish steps"
    )


def test_publish_steps_fail_loudly_on_empty_dist() -> None:
    """Each publish step must exit 1 when its dist/ is empty so a missing
    upstream build isn't silently swallowed.
    """
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    # Each of the three publish steps has its own `exit 1` branch in the
    # `else` clause; count those instead of looking for a single pattern.
    assert content.count("exit 1") >= 3, (
        "each publish step must `exit 1` when no wheels are present; "
        "found fewer than 3 `exit 1` clauses"
    )


def test_tag_release_step_bumps_all_three_pyprojects() -> None:
    """The version bumper must update every pyproject so the released
    triple ships with coherent versions."""
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    for pyproject in (
        "packaging/revue_core/pyproject.toml",
        "packaging/revue-ci/pyproject.toml",
        "packaging/revue/pyproject.toml",
    ):
        assert pyproject in content, (
            f"tag-release sed must include {pyproject}"
        )


def test_revue_ci_build_scripts_exist() -> None:
    """The revue-ci Nuitka build path must exist on disk — the pipeline
    invokes it directly."""
    for relative in (
        "packaging/revue-ci/build/build_nuitka.py",
        "packaging/revue-ci/build/build_wheel.py",
    ):
        path = REPO_ROOT / relative
        assert path.is_file(), f"missing build script: {path}"


# ---------------------------------------------------------------------------
# REVUE-310 follow-up — revue_core is IP and ships Nuitka-compiled per platform.
# See memory feedback_revue_core_nuitka.md. Plain-Python distribution of
# revue_core leaks IP through the source .py files on PyPI.
# ---------------------------------------------------------------------------


def test_revue_core_build_scripts_exist() -> None:
    """revue_core must own a Nuitka build path matching revue-ci's shape."""
    for relative in (
        "packaging/revue_core/build/build_nuitka.py",
        "packaging/revue_core/build/build_wheel.py",
    ):
        path = REPO_ROOT / relative
        assert path.is_file(), f"missing build script: {path}"


def test_revue_core_pipeline_uses_nuitka_per_platform() -> None:
    """The tag pipeline must build revue_core via Nuitka on macOS + Linux —
    not via the old hatchling pure-Python step. Step names follow the same
    `Build <platform> — <pkg>` shape used for revue-ci and the skill wheel."""
    names = _tag_step_names()
    revue_core_steps = [n for n in names if "revue_core" in n.lower()]
    assert any("macos" in n.lower() for n in revue_core_steps), (
        f"tag pipeline missing a macOS Nuitka build step for revue_core. "
        f"revue_core steps found: {revue_core_steps}"
    )
    assert any("linux" in n.lower() for n in revue_core_steps), (
        f"tag pipeline missing a Linux Nuitka build step for revue_core. "
        f"revue_core steps found: {revue_core_steps}"
    )


def test_revue_core_pipeline_invokes_build_nuitka_script() -> None:
    """Both revue_core build steps must call our build_nuitka.py + build_wheel.py
    — guards against a regression to `python -m build` (hatchling pure-Python)."""
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    for needed in (
        "packaging/revue_core/build/build_nuitka.py",
        "packaging/revue_core/build/build_wheel.py",
    ):
        assert needed in content, (
            f"bitbucket-pipelines.yml must invoke {needed} for the revue_core "
            f"build steps; otherwise the published wheel is pure-Python and "
            f"exposes IP."
        )


def test_revue_core_publish_step_reads_from_wheels_dir() -> None:
    """The Nuitka-compiled wheel lands in dist/wheels/, not dist/ (which was
    the hatchling layout). The publish step must read from the new location."""
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    assert "packaging/revue_core/dist/wheels/" in content, (
        "Publish revue_core step must read Nuitka wheels from dist/wheels/, "
        "not the hatchling dist/ root."
    )


def test_pipeline_does_not_claim_pure_python_revue_core() -> None:
    """Belt-and-braces: a comment in the YAML that says revue_core ships as
    `pure-Python` or `no Nuitka` directly contradicts the IP-protection
    requirement and must not survive into main."""
    content = PIPELINES_FILE.read_text(encoding="utf-8").lower()
    forbidden_phrases = (
        "no nuitka",
        "pure-python wheel builds for revue_core",
        "pure python wheel builds for revue_core",
    )
    leaked = [p for p in forbidden_phrases if p in content]
    assert not leaked, (
        "bitbucket-pipelines.yml still describes revue_core as pure-Python: "
        f"found phrases {leaked}. revue_core must be Nuitka-compiled per platform."
    )

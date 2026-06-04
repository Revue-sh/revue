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
    """Flatten the tag pipeline's step list — including steps nested inside a
    `parallel:` block — into a single ordered list of step names. The order
    within a parallel block is preserved from the YAML; execution-wise those
    steps run concurrently, but we still need a total order to express the
    "builds-block precedes publishes-block" invariant.
    """
    doc = yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))
    names: list[str] = []
    for entry in doc["pipelines"]["tags"]["v*"]:
        if "step" in entry:
            names.append(entry["step"].get("name", ""))
        elif "parallel" in entry:
            parallel = entry["parallel"]
            inner = parallel if isinstance(parallel, list) else parallel.get("steps", [])
            for sub in inner:
                if "step" in sub:
                    names.append(sub["step"].get("name", ""))
    return names


def _first_index_matching(names: list[str], substr: str) -> int:
    for i, n in enumerate(names):
        if substr.lower() in n.lower():
            return i
    pytest.fail(
        f"tag pipeline has no step matching '{substr}'. Steps were: {names}"
    )


def test_all_builds_precede_all_publishes() -> None:
    """Every Nuitka build step must appear before every Publish step in the
    tag pipeline. Builds may run in a `parallel:` block (REVUE-323), but the
    publish chain that follows must wait for the whole block to finish so
    that every published package has both platform wheels available.
    """
    names = _tag_step_names()
    build_indices = [i for i, n in enumerate(names) if n.lower().startswith("build ")]
    publish_indices = [i for i, n in enumerate(names) if n.lower().startswith("publish ")]
    assert build_indices, f"tag pipeline has no Build steps; names={names}"
    assert publish_indices, f"tag pipeline has no Publish steps; names={names}"
    assert max(build_indices) < min(publish_indices), (
        "all Build steps must precede all Publish steps in the tag pipeline. "
        f"Builds at {build_indices}, publishes at {publish_indices}; names={names}"
    )


def test_tag_pipeline_uses_parallel_build_block() -> None:
    """Codify the REVUE-323 layout: the 6 Nuitka build steps live inside a
    single `parallel:` block so cloud Linux runners build concurrently.
    Without this block, the pipeline reverts to ~30 min sequential builds.

    The tag pipeline also has a SECOND parallel block — the REVUE-393 unit+web
    test gate — so the build block is located by its contents (steps named
    "Build …"), not by being the only parallel block.
    """
    doc = yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))
    entries = doc["pipelines"]["tags"]["v*"]
    parallel_blocks = [e["parallel"] for e in entries if "parallel" in e]

    def _inner_names(block: Any) -> list[str]:
        inner = block if isinstance(block, list) else block.get("steps", [])
        return [s["step"].get("name", "") for s in inner if "step" in s]

    build_blocks = [
        b for b in parallel_blocks
        if any(n.lower().startswith("build ") for n in _inner_names(b))
    ]
    assert len(build_blocks) == 1, (
        f"expected exactly one parallel BUILD block in the tag pipeline; "
        f"found {len(build_blocks)}"
    )
    build_names = [n for n in _inner_names(build_blocks[0]) if n.lower().startswith("build ")]
    assert len(build_names) == 6, (
        f"parallel block must hold the 6 Nuitka build steps (3 packages × 2 "
        f"platforms); got {len(build_names)}: {build_names}"
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

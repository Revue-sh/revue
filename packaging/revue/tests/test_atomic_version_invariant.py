"""REVUE-322 — the three packages must release atomically.

The Tag Release pipeline step bumps `version = "..."` in all three
pyproject.tomls AND must also bump the cross-package ``revue_core~=...``
pin in ``revue-ci`` and ``revue``. Otherwise the tag pipeline fails at
``pip install -e`` because e.g. ``revue_core==0.18.1`` does not satisfy
``revue_core~=0.1.0``.

This test locks two halves of the invariant:

1. **File invariant** — at every commit, all three pyproject.tomls share
   the same ``version``, and the downstream ``revue_core~=X.Y.Z`` pin
   matches that ``X.Y.Z``.
2. **Script invariant** — the Tag Release step in
   ``bitbucket-pipelines.yml`` contains sed substitutions that update the
   cross-package pin, not just the package ``version`` field.

Both are needed: the file invariant catches a stale commit; the script
invariant catches a script regression that would only surface on the next
release.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_CORE = REPO_ROOT / "packaging" / "revue_core" / "pyproject.toml"
PYPROJECT_CI = REPO_ROOT / "packaging" / "revue-ci" / "pyproject.toml"
PYPROJECT_SKILL = REPO_ROOT / "packaging" / "revue" / "pyproject.toml"
PIPELINES_FILE = REPO_ROOT / "bitbucket-pipelines.yml"

PIN_RE = re.compile(r"revue_core~=(?P<version>\S+)")


def _project_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["project"]["version"]


def _revue_core_pin(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    for req in deps:
        match = PIN_RE.fullmatch(req.strip())
        if match:
            return match.group("version")
    raise AssertionError(
        f"{path} declares no `revue_core~=X.Y.Z` pin in [project.dependencies]; "
        f"found {deps}"
    )


def test_all_three_packages_share_one_version() -> None:
    v_core = _project_version(PYPROJECT_CORE)
    v_ci = _project_version(PYPROJECT_CI)
    v_skill = _project_version(PYPROJECT_SKILL)
    assert v_core == v_ci == v_skill, (
        "revue_core, revue-ci, and revue must share a single version. "
        f"Got revue_core={v_core}, revue-ci={v_ci}, revue={v_skill}. "
        "The Tag Release pipeline step is the only place these should ever "
        "be bumped, and it bumps all three together."
    )


def test_downstream_pins_match_revue_core_version() -> None:
    """Cross-package pin ``revue_core~=X.Y.Z`` must match revue_core's
    actual version. Otherwise ``pip install -e`` cannot resolve the set —
    the failure mode that broke tag pipeline #834."""
    core_version = _project_version(PYPROJECT_CORE)
    ci_pin = _revue_core_pin(PYPROJECT_CI)
    skill_pin = _revue_core_pin(PYPROJECT_SKILL)
    assert ci_pin == core_version, (
        f"packaging/revue-ci/pyproject.toml pins revue_core~={ci_pin}, but "
        f"revue_core's actual version is {core_version}. Tag Release must "
        "bump both."
    )
    assert skill_pin == core_version, (
        f"packaging/revue/pyproject.toml pins revue_core~={skill_pin}, but "
        f"revue_core's actual version is {core_version}. Tag Release must "
        "bump both."
    )


def test_tag_release_step_updates_cross_package_pins() -> None:
    """The Tag Release pipeline step is the only place version bumps live.
    It must update the cross-package pin in addition to the package version,
    or every published release will fail to install."""
    yaml_text = PIPELINES_FILE.read_text(encoding="utf-8")
    # Match both single- and double-quoted requirement strings; the actual
    # sed must target the `revue_core~=` token in both downstream pyprojects.
    pattern = re.compile(
        r"sed\s+-i\b[^\n]*revue_core~=[^\n]*packaging/revue-ci/pyproject\.toml",
    )
    assert pattern.search(yaml_text), (
        "bitbucket-pipelines.yml Tag Release step must contain a sed command "
        "that rewrites `revue_core~=...` in packaging/revue-ci/pyproject.toml. "
        "Without it, the tag pipeline fails pip resolution after the version bump."
    )
    pattern_skill = re.compile(
        r"sed\s+-i\b[^\n]*revue_core~=[^\n]*packaging/revue/pyproject\.toml",
    )
    assert pattern_skill.search(yaml_text), (
        "bitbucket-pipelines.yml Tag Release step must contain a sed command "
        "that rewrites `revue_core~=...` in packaging/revue/pyproject.toml."
    )

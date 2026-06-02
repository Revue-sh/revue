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


def _skill_init_version() -> str:
    """Extract __version__ from packaging/revue/src/revue_skill/__init__.py."""
    init_file = REPO_ROOT / "packaging" / "revue" / "src" / "revue_skill" / "__init__.py"
    init_text = init_file.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if not match:
        raise AssertionError(f"Cannot find __version__ in {init_file}")
    return match.group(1)


def test_skill_init_version_matches_pyproject() -> None:
    """REVUE-372 file invariant: __init__.py __version__ must match pyproject.toml.

    The customer-side install-skill strict version check compares
    manifest["current_version"] against the installed wheel's __version__.
    If __init__.py is stale, the check fails on every release after 0.1.0.
    """
    pyproject_version = _project_version(PYPROJECT_SKILL)
    init_version = _skill_init_version()
    assert init_version == pyproject_version, (
        f"packaging/revue/src/revue_skill/__init__.py declares __version__ = "
        f'"{init_version}", but packaging/revue/pyproject.toml declares '
        f'version = "{pyproject_version}". The Tag Release pipeline must bump '
        "both, or customer install-skill will reject the release."
    )


def test_tag_release_step_updates_skill_init_version() -> None:
    """REVUE-372 script invariant: Tag Release must sed __init__.py.

    The pipeline sed step must update packaging/revue/src/revue_skill/__init__.py
    to match the new version, or the published wheel will carry stale __version__
    metadata and fail the customer's strict version check.
    """
    yaml_text = PIPELINES_FILE.read_text(encoding="utf-8")
    pattern = re.compile(
        r"sed\s+-i\b[^\n]*__version__[^\n]*packaging/revue/src/revue_skill/__init__\.py",
    )
    init_sed = pattern.search(yaml_text)
    assert init_sed, (
        "bitbucket-pipelines.yml Tag Release step must contain a sed command "
        "that rewrites `__version__ = \"...\"` in "
        "packaging/revue/src/revue_skill/__init__.py. Without it, every "
        "published release after 0.1.0 will ship with stale __version__ "
        "and fail the customer-side strict version check."
    )

    # The sed must run BEFORE the commit that gets tagged, or the bump never
    # reaches the published artefact (L5). The sed line is no longer adjacent
    # to git add — it sits among five seds — so lock the ordering explicitly.
    git_commit = re.search(r"git commit -m .*release", yaml_text)
    assert git_commit, "Tag Release step must contain the `git commit` release line"
    assert init_sed.start() < git_commit.start(), (
        "The __version__ sed must precede `git commit` in the Tag Release step; "
        "otherwise the bump is committed/tagged stale and ships the wrong version."
    )


def test_pipeline_init_sed_actually_bumps_current_init_file() -> None:
    """REVUE-372 AC2 (behavioural): the pipeline's own __version__ sed
    expression must actually match the line format in __init__.py.

    The static grep above proves the sed *line exists*; it does not prove the
    sed *pattern matches* the real ``__version__ = "..."`` line. A malformed
    pattern (wrong spacing, missing anchor) would pass the grep yet rewrite
    nothing at release time — shipping a stale version with green tests.

    This extracts the pipeline's exact sed program, substitutes a target
    version for ``${NEXT_VERSION}``, applies it to the live __init__.py, and
    asserts exactly one substitution lands the target version.
    """
    yaml_text = PIPELINES_FILE.read_text(encoding="utf-8")
    init_file = REPO_ROOT / "packaging" / "revue" / "src" / "revue_skill" / "__init__.py"

    # Pull the sed program out of: sed -i "s/.../.../" packaging/.../__init__.py
    # In the YAML literal block the inner quotes are backslash-escaped (\"),
    # which the shell unescapes to " before sed sees them.
    sed_line = re.search(
        r'sed\s+-i\s+"(?P<prog>s/.*?__version__.*?/)"\s+'
        r"packaging/revue/src/revue_skill/__init__\.py",
        yaml_text,
    )
    assert sed_line, "Could not extract the __version__ sed program from the pipeline"

    target = "9.9.9"
    prog = sed_line.group("prog").replace('\\"', '"').replace("${NEXT_VERSION}", target)
    # Lock the canonical sed form so a quoting/format drift in the pipeline
    # fails HERE with a clear message, rather than misdirecting to the count
    # assertion below (M3).
    assert prog == f's/^__version__ = "[^"]*"/__version__ = "{target}"/', (
        f"Pipeline __version__ sed program changed shape: {prog!r}. Update this "
        "test deliberately if the pipeline's substitution was intentionally "
        "reworked."
    )
    # Delimiter-agnostic parse of s<delim>LHS<delim>RHS<delim> so an alternate
    # sed delimiter degrades to a readable failure instead of a ValueError (M2).
    parsed = re.fullmatch(r"s(.)(?P<lhs>.*)\1(?P<rhs>.*)\1", prog)
    assert parsed, f"sed program is not in s/LHS/RHS/ form: {prog!r}"
    lhs, rhs = parsed.group("lhs"), parsed.group("rhs")

    original = init_file.read_text(encoding="utf-8")
    bumped, count = re.subn(lhs, rhs, original, flags=re.MULTILINE)
    assert count == 1, (
        f"The pipeline's __version__ sed pattern `{lhs}` matched {count} lines "
        f"in {init_file} (expected exactly 1). The pattern no longer matches the "
        "file's line format — release-time bump would be a no-op."
    )
    assert f'__version__ = "{target}"' in bumped
    # Prove the sed actually rewrote a value — guards against a future state
    # where the live file already sits at the target and a no-op would pass (L4).
    assert bumped != original, (
        "sed produced no change; the behavioural test must exercise a real "
        f"version transition (live file must not already be at {target})."
    )

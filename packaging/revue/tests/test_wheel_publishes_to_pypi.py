"""Test: Bitbucket Pipelines wires up a Nuitka build + PyPI publish for the skill wheel (AC3, AC4).

We cannot test the actual build or publish here — those run in CI on macOS ARM64
(self-hosted) and Linux x86_64 (managed) runners. What we verify locally is that
the pipeline declaration is correct and complete.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
PIPELINES_FILE = REPO_ROOT / "bitbucket-pipelines.yml"


def _load_pipeline() -> dict:
    return yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))


def test_pipelines_file_exists() -> None:
    assert PIPELINES_FILE.is_file(), f"missing: {PIPELINES_FILE}"


def test_tag_pipeline_has_skill_build_macos() -> None:
    pipeline = _load_pipeline()
    tag_steps = pipeline["pipelines"]["tags"]["v*"]
    names = [s.get("step", {}).get("name", "") for s in tag_steps]
    assert any("revue skill" in n.lower() or "skill" in n.lower() for n in names), (
        f"tag pipeline must include a skill-build macOS step; found: {names}"
    )


def test_tag_pipeline_has_skill_build_linux() -> None:
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    assert "build_nuitka.py" in content, (
        "bitbucket-pipelines.yml must invoke packaging/revue/build/build_nuitka.py"
    )
    assert "build_wheel.py" in content, (
        "bitbucket-pipelines.yml must invoke packaging/revue/build/build_wheel.py"
    )


def test_tag_pipeline_publishes_to_pypi() -> None:
    content = PIPELINES_FILE.read_text(encoding="utf-8")
    assert "PYPI_API_TOKEN" in content, (
        "bitbucket-pipelines.yml must reference PYPI_API_TOKEN for PyPI publish"
    )
    assert "twine" in content, (
        "bitbucket-pipelines.yml must use twine to upload wheels to PyPI"
    )


def test_build_nuitka_script_exists() -> None:
    script = PACKAGING_DIR / "build" / "build_nuitka.py"
    assert script.is_file(), f"missing Nuitka build script: {script}"


def test_build_wheel_script_exists() -> None:
    script = PACKAGING_DIR / "build" / "build_wheel.py"
    assert script.is_file(), f"missing wheel assembly script: {script}"


def test_vendor_sources_script_exists() -> None:
    script = PACKAGING_DIR / "tools" / "vendor_sources.py"
    assert script.is_file(), f"missing vendor script: {script}"

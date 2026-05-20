"""REVUE-310 — Fresh-venv smoke test for the 3-package install graph.

Validates that `pip install -e packaging/revue_core/ -e packaging/revue-ci/
-e packaging/revue/` resolves cleanly in an empty venv and the three
top-level entry points / module imports work end-to-end.

The test is slow (creates a venv, downloads transitive deps), so it is
gated behind the ``slow`` marker. Run with::

    pytest tests/integration/test_fresh_venv_install.py -m slow

Single-platform: this test exercises only the platform it's running on.
Cross-platform validation lives in the Bitbucket Pipelines macOS / Linux
build steps. The intent here is to catch package-graph regressions
locally before a tag is cut.
"""
from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING = REPO_ROOT / "packaging"


@pytest.mark.slow
def test_fresh_venv_can_install_all_three_packages(tmp_path: Path) -> None:
    """A fresh venv pip-installs revue_core + revue-ci + revue and runs the CLI."""
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    pip = venv_dir / "bin" / "pip"
    py = venv_dir / "bin" / "python"
    revue_ci = venv_dir / "bin" / "revue-ci"

    assert pip.exists(), f"pip not present in fresh venv at {pip}"

    # 1. revue_core (the leaf) installs without any local dep
    subprocess.run(
        [str(pip), "install", "--quiet", "-e", str(PACKAGING / "revue_core")],
        check=True,
    )

    # 2. revue-ci installs on top — pulls revue_core via the editable install
    subprocess.run(
        [str(pip), "install", "--quiet", "-e", str(PACKAGING / "revue-ci")],
        check=True,
    )

    # 3. revue (skill wheel) installs — independent of revue-ci, shares revue_core
    subprocess.run(
        [str(pip), "install", "--quiet", "-e", str(PACKAGING / "revue")],
        check=True,
    )

    # Smoke-import each
    for module in ("revue_core", "revue_ci", "revue_skill"):
        result = subprocess.run(
            [str(py), "-c", f"import {module}"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"`import {module}` failed in fresh venv:\n{result.stderr}"
        )

    # CLI entry point resolves and responds to --help
    assert revue_ci.exists(), f"revue-ci entry point not installed at {revue_ci}"
    result = subprocess.run(
        [str(revue_ci), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"`revue-ci --help` exited {result.returncode}:\n{result.stderr}"
    )
    assert "review" in result.stdout, (
        f"`revue-ci --help` is missing the 'review' subcommand:\n{result.stdout}"
    )

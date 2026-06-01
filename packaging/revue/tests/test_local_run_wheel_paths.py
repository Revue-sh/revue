"""REVUE-369 H1 (Codex finding): in the wheel, local_run.py must resolve
REPO_ROOT to the customer's git checkout (where `git diff` runs) and
WHEEL_ASSETS_DIR to the wheel's bundled assets dir (where _revue/agents lives).

Before the fix: Path(__file__).resolve().parent.parent in the wheel returned
revue_skill/, so git diff ran in the wheel install dir and _revue/agents
lookups pointed at non-existent paths.

After the fix (via vendor-time rewrite in sources.yaml):
- WHEEL_ASSETS_DIR = Path(__file__).parent  →  revue_skill/skill/
- REPO_ROOT = `git rev-parse --show-toplevel` (falls back to cwd)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
VENDORED_LOCAL_RUN = PACKAGING_DIR / "src" / "revue_skill" / "skill" / "local_run.py"


def test_vendored_local_run_has_wheel_assets_dir():
    # Arrange — read the vendored copy (already gone through vendor-time rewrites)
    source = VENDORED_LOCAL_RUN.read_text(encoding="utf-8")

    # Assert — WHEEL_ASSETS_DIR is defined and points to the module's parent
    assert "WHEEL_ASSETS_DIR = Path(__file__).resolve().parent" in source, (
        "Vendored local_run.py must define WHEEL_ASSETS_DIR for bundled assets "
        "(REVUE-369 H1)"
    )
    # Assert — old single-path REPO_ROOT definition has been rewritten out
    assert "REPO_ROOT = Path(__file__).resolve().parent.parent" not in source, (
        "Source-tree REPO_ROOT computation must NOT remain in the vendored copy"
    )
    # Assert — new REPO_ROOT uses git rev-parse to find the customer's repo
    assert 'git rev-parse --show-toplevel' in source.replace('"', "").replace("'", "") or '"git", "rev-parse", "--show-toplevel"' in source, (
        "Vendored REPO_ROOT must resolve via git rev-parse --show-toplevel"
    )


def test_vendored_local_run_agent_paths_use_wheel_assets():
    # Arrange
    source = VENDORED_LOCAL_RUN.read_text(encoding="utf-8")

    # Assert — every _revue/agents reference uses WHEEL_ASSETS_DIR, not REPO_ROOT
    assert 'REPO_ROOT / "_revue/agents"' not in source, (
        "Bundled agent prompts must be looked up via WHEEL_ASSETS_DIR in the wheel "
        "(REPO_ROOT in the wheel is the customer's git checkout, not the bundle)"
    )
    assert 'WHEEL_ASSETS_DIR / "_revue/agents"' in source, (
        "Vendored local_run.py must look up bundled agent prompts via WHEEL_ASSETS_DIR"
    )


def test_vendored_local_run_repo_root_resolves_to_customer_repo(tmp_path, monkeypatch):
    # Arrange — invoke the vendored local_run.py in a fake customer repo,
    # confirming REPO_ROOT picks up the customer's checkout via git rev-parse
    customer_repo = tmp_path / "customer-repo"
    customer_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=customer_repo, check=True)
    # Make a commit so `git rev-parse --show-toplevel` succeeds reliably
    (customer_repo / "README.md").write_text("# Customer project\n")
    subprocess.run(["git", "add", "."], cwd=customer_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@test", "-c", "user.name=Test", "commit", "-q", "-m", "init"],
        cwd=customer_repo,
        check=True,
    )

    # Act — execute a tiny script that imports the vendored module
    # and prints REPO_ROOT + WHEEL_ASSETS_DIR
    src_dir = PACKAGING_DIR / "src"
    probe_script = (
        f"import sys; sys.path.insert(0, {str(src_dir)!r});\n"
        "from revue_skill.skill import local_run as lr;\n"
        "print('REPO_ROOT', lr.REPO_ROOT);\n"
        "print('WHEEL_ASSETS_DIR', lr.WHEEL_ASSETS_DIR);\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe_script],
        cwd=customer_repo,
        capture_output=True,
        text=True,
    )

    # Assert — REPO_ROOT is the customer's repo (resolved via git rev-parse),
    # WHEEL_ASSETS_DIR is the revue_skill/skill/ install location
    assert result.returncode == 0, f"Probe failed: {result.stderr}"
    expected_repo_root = str(customer_repo.resolve())
    assert f"REPO_ROOT {expected_repo_root}" in result.stdout, (
        f"REPO_ROOT must resolve to customer repo {expected_repo_root}, got:\n{result.stdout}"
    )
    expected_wheel_assets = str(VENDORED_LOCAL_RUN.parent.resolve())
    assert f"WHEEL_ASSETS_DIR {expected_wheel_assets}" in result.stdout, (
        f"WHEEL_ASSETS_DIR must point at the bundled skill dir {expected_wheel_assets}, "
        f"got:\n{result.stdout}"
    )


def test_vendored_local_run_repo_root_falls_back_to_cwd_outside_git(tmp_path):
    # Arrange — invoke from a non-git directory; REPO_ROOT should fall back to cwd
    non_git_dir = tmp_path / "no-git"
    non_git_dir.mkdir()

    src_dir = PACKAGING_DIR / "src"
    probe_script = (
        f"import sys; sys.path.insert(0, {str(src_dir)!r});\n"
        "from revue_skill.skill import local_run as lr;\n"
        "print('REPO_ROOT', lr.REPO_ROOT);\n"
    )

    # Act
    result = subprocess.run(
        [sys.executable, "-c", probe_script],
        cwd=non_git_dir,
        capture_output=True,
        text=True,
    )

    # Assert — fallback path returns cwd
    assert result.returncode == 0, f"Probe failed: {result.stderr}"
    expected = str(non_git_dir.resolve())
    assert f"REPO_ROOT {expected}" in result.stdout, (
        f"REPO_ROOT must fall back to cwd when not in a git repo, got:\n{result.stdout}"
    )

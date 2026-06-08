"""Cross-component contract tests for the fixed licence-file location."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_SOURCE = REPO_ROOT / "packaging" / "revue" / "src"


def test_no_shipped_source_advertises_revue_licence_path():
    """Shipped production source (src/ tree) contains no REVUE_LICENCE_PATH references.

    Tests are intentionally excluded: two test files set REVUE_LICENCE_PATH to
    prove it is ignored, which is correct usage, not an override contract.
    """
    # Arrange
    source_files = sorted(SHIPPED_SOURCE.rglob("*.py"))

    # Act
    matches = [
        path.relative_to(REPO_ROOT)
        for path in source_files
        if "REVUE_LICENCE_PATH" in path.read_text()
    ]

    # Assert
    assert matches == []


def _get_tracked_docs() -> list[str]:
    return subprocess.run(
        ["git", "ls-files", "docs/*.md", "docs/**/*.md"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def test_tracked_docs_do_not_advertise_revue_licence_path():
    """Tracked documentation contains no REVUE_LICENCE_PATH override contract."""
    # Arrange
    tracked_docs = _get_tracked_docs()

    # Act
    matches = [
        relative_path
        for relative_path in tracked_docs
        if "REVUE_LICENCE_PATH" in (REPO_ROOT / relative_path).read_text()
    ]

    # Assert
    assert matches == []

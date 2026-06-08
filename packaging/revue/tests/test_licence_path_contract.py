"""Cross-component contract tests for the fixed licence-file location."""

from __future__ import annotations

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


def test_tracked_docs_do_not_advertise_revue_licence_path():
    """Documentation under docs/ contains no REVUE_LICENCE_PATH override contract."""
    # Arrange — glob directly; avoids git subprocess which fails in container CI
    # (bind-mount filesystem boundary prevents git ls-files from working)
    doc_files = sorted((REPO_ROOT / "docs").rglob("*.md"))

    # Act
    matches = [
        str(p.relative_to(REPO_ROOT))
        for p in doc_files
        if "REVUE_LICENCE_PATH" in p.read_text()
    ]

    # Assert
    assert matches == []

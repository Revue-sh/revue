"""Tests for vendor_sources.py path-traversal protection (REVUE-275 medium finding).

``sources.yaml`` is checked into the repo, but if a contributor (or a malicious
PR) sets ``source: ../../etc/passwd`` or ``target: ../somewhere-else``, the
vendor script would happily copy outside the intended trees. We validate that
resolved paths stay under REPO_ROOT (for sources) and PACKAGING_DIR (for
targets).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PACKAGING_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PACKAGING_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import vendor_sources as vs  # noqa: E402  (import after sys.path tweak)


def test_resolve_safe_source_path() -> None:
    """A legitimate relative path under REPO_ROOT is accepted."""
    resolved = vs._safe_join(vs.REPO_ROOT, "src/revue/__init__.py")
    assert resolved.is_relative_to(vs.REPO_ROOT)


def test_resolve_safe_target_path() -> None:
    """A legitimate relative target under PACKAGING_DIR is accepted."""
    resolved = vs._safe_join(vs.PACKAGING_DIR, "src/revue_skill/vendored/foo.py")
    assert resolved.is_relative_to(vs.PACKAGING_DIR)


def test_dotdot_source_rejected() -> None:
    with pytest.raises(vs.UnsafePathError):
        vs._safe_join(vs.REPO_ROOT, "../etc/passwd")


def test_absolute_source_rejected() -> None:
    with pytest.raises(vs.UnsafePathError):
        vs._safe_join(vs.REPO_ROOT, "/etc/passwd")


def test_dotdot_in_middle_rejected() -> None:
    with pytest.raises(vs.UnsafePathError):
        vs._safe_join(vs.PACKAGING_DIR, "src/../../etc/passwd")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    """If the relative segment resolves outside the base, reject."""
    with pytest.raises(vs.UnsafePathError):
        vs._safe_join(tmp_path, "../escape")

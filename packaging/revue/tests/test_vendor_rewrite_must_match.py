"""Tests for vendor_sources.py rewrite-match enforcement (REVUE-370 M1 finding).

``rewrite_imports`` rules are applied as plain ``str.replace`` substitutions. A
``str.replace`` whose ``from`` literal is absent is a silent no-op: if the
canonical source text ever drifts from a ``from`` literal in ``sources.yaml``
(a whitespace, quote, or wording change), the rewrite quietly does nothing and
the wheel ships *unrewritten* source — including, for the REVUE-370 rule, the
``REVUE_SKIP_LICENCE_CHECK`` bypass. The vendor step must fail loudly when a
rewrite matches nothing rather than emit a clean-looking but unrewritten file.
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


def _entry(source: Path, target: Path, rewrites) -> vs.FileEntry:
    return vs.FileEntry(source=source, target=target, rewrites=tuple(rewrites))


def test_apply_raises_when_rewrite_from_is_absent(tmp_path: Path) -> None:
    """A rewrite whose `from` literal is not in the source fails loudly."""
    source = tmp_path / "src.py"
    source.write_text("the source text without the literal\n", encoding="utf-8")
    target = tmp_path / "out.py"

    entry = _entry(source, target, [("NONEXISTENT_LITERAL", "replacement")])

    with pytest.raises(vs.RewriteNotFoundError):
        vs._apply(entry)


def test_apply_does_not_write_target_on_unmatched_rewrite(tmp_path: Path) -> None:
    """A no-op rewrite must not leave a half-written (unrewritten) target."""
    source = tmp_path / "src.py"
    source.write_text("plain source\n", encoding="utf-8")
    target = tmp_path / "out.py"

    entry = _entry(source, target, [("ABSENT", "x")])

    with pytest.raises(vs.RewriteNotFoundError):
        vs._apply(entry)
    assert not target.exists()


def test_apply_applies_matching_rewrite(tmp_path: Path) -> None:
    """A rewrite whose `from` literal is present is applied normally."""
    source = tmp_path / "src.py"
    source.write_text("hello there\n", encoding="utf-8")
    target = tmp_path / "out.py"

    entry = _entry(source, target, [("hello", "world")])
    vs._apply(entry)

    assert target.read_text(encoding="utf-8") == "world there\n"

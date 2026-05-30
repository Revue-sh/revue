"""Guard: every revue_skill source module ships in the Nuitka wheel.

REVUE-359 regression guard. A module imported at top level by a compiled
module (cli/activate/validate/skill/local_run) but absent from
``COMPILE_ROOTS`` is neither compiled nor copied into ``dist/compiled/``, so
it vanishes from the published wheel and the CLI ImportErrors at startup —
while the editable-source test suite stays green.

Two slips are pinned here:

1. ``support.py`` (top level) — caught by the original REVUE-359 review.
2. ``skill/post_review_signals.py`` — surfaced by the /code-review high pass
   after the first fix. The skill/ copytree in ``build_nuitka.main`` uses
   ``ignore_patterns('*.py', '__pycache__')``, so any ``skill/*.py`` not in
   ``COMPILE_ROOTS`` is dropped from the wheel exactly like a missing
   top-level module.

The invariant we enforce: every ``revue_skill/*.py`` AND ``revue_skill/skill/*.py``
source module (except ``__init__.py``, which is copied as plain Python) must
be listed in ``build_nuitka.COMPILE_ROOTS``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PACKAGING_DIR / "src" / "revue_skill"
SKILL_DIR = SRC_DIR / "skill"
BUILD_NUITKA = PACKAGING_DIR / "build" / "build_nuitka.py"


def _load_compile_roots() -> list[Path]:
    """Import build_nuitka.py by path and return the COMPILE_ROOTS paths.

    Imported under a private name via spec_from_file_location so the module's
    ``if __name__ == "__main__"`` guard does not run main() as a side effect.
    """
    spec = importlib.util.spec_from_file_location("_build_nuitka", BUILD_NUITKA)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [Path(p) for p in module.COMPILE_ROOTS]


def test_every_top_level_module_is_in_compile_roots():
    # Arrange — top-level source modules that must ship in the wheel. Compare
    # by FULL path (not basename) restricted to the SRC_DIR root, so a future
    # top-level module that happens to share a filename with a skill/ entry
    # cannot satisfy the check vacuously.
    top_level = {
        p.resolve() for p in SRC_DIR.glob("*.py") if p.name != "__init__.py"
    }
    compiled_top_level = {
        p.resolve() for p in _load_compile_roots() if p.parent == SRC_DIR
    }

    # Act
    missing = top_level - compiled_top_level

    # Assert — none may be absent (would vanish from the published wheel)
    assert missing == set(), (
        f"top-level modules missing from COMPILE_ROOTS (will not ship in the "
        f"wheel → ImportError at CLI startup): {sorted(str(p) for p in missing)}"
    )
    # Guard against a vacuous pass (empty glob or empty roots).
    assert top_level, "no top-level modules discovered — test is mis-pathed"


def test_every_skill_module_is_in_compile_roots():
    # Arrange — skill/*.py source modules that must ship in the wheel. Same
    # rationale as the top-level test: build_nuitka.main's skill/ copytree
    # excludes *.py via ignore_patterns, so any skill/*.py not in
    # COMPILE_ROOTS is dropped from the wheel entirely.
    skill_modules = {
        p.resolve() for p in SKILL_DIR.glob("*.py") if p.name != "__init__.py"
    }
    compiled_skill_modules = {
        p.resolve() for p in _load_compile_roots() if p.parent == SKILL_DIR
    }

    # Act
    missing = skill_modules - compiled_skill_modules

    # Assert
    assert missing == set(), (
        f"skill/*.py modules missing from COMPILE_ROOTS (excluded by the "
        f"skill/ copytree's ignore_patterns → ImportError at runtime): "
        f"{sorted(str(p) for p in missing)}"
    )
    # Guard against a vacuous pass.
    assert skill_modules, "no skill/ modules discovered — test is mis-pathed"


def test_support_module_specifically_is_compiled():
    # Arrange / Act — support.py is the REVUE-359 module that slipped the gap
    compiled = {p.resolve() for p in _load_compile_roots()}

    # Assert — pinned by full path under SRC_DIR
    assert (SRC_DIR / "support.py").resolve() in compiled


def test_post_review_signals_specifically_is_compiled():
    # Arrange / Act — post_review_signals.py was the second slip surfaced by
    # the /code-review high pass after the initial REVUE-359 fix; pin it
    # explicitly so a future refactor that "cleans up" the COMPILE_ROOTS list
    # cannot silently drop it.
    compiled = {p.resolve() for p in _load_compile_roots()}

    # Assert
    assert (SKILL_DIR / "post_review_signals.py").resolve() in compiled

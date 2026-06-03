"""Guard: the embedded JWT key cannot be cross-module constant-folded because
both wheels compile per-file with ``nuitka --module``.

REVUE-334 investigated the hypothesis that Nuitka constant-folds the embedded
``JWT_PUBLIC_KEY_PEM`` from ``revue_core.security.jwt_keys`` into the compiled
bodies of the verify sites (``activate.py`` / ``validate.py``, shipped in the
separate ``revue`` wheel), which would let a key rotation that rebuilt only
``revue_core`` leave the verify sites checking against a stale, folded key.

An empirical Nuitka experiment (documented in ``README.md`` and the ticket)
settled it: under ``--module`` mode each ``.py`` is compiled as an *independent*
unit. At ``activate.py``'s compile time the imported ``jwt_keys`` module is
opaque, so ``_jwt_keys.JWT_PUBLIC_KEY_PEM`` necessarily becomes a runtime
attribute lookup. Recompiling only ``jwt_keys`` with a substitute key is
observed by the *unchanged* caller binary — i.e. there is no cross-module
folding to defend against. The ``get_jwt_public_key()`` accessor and the
verify-site AST guard are therefore defensive clarity / future-proofing, not a
fix for a live vulnerability.

That no-folding property holds ONLY while the build stays per-module. A switch
to whole-program ``--standalone`` / ``--onefile`` mode could, in principle,
make cross-module folding possible within a single compilation. This test pins
the build mode so such a switch fails loudly in CI, prompting a re-evaluation
of whether the accessor has become load-bearing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Both build scripts whose compilation mode the no-folding property depends on.
BUILD_NUITKA_SCRIPTS = (
    REPO_ROOT / "packaging" / "revue_core" / "build" / "build_nuitka.py",
    REPO_ROOT / "packaging" / "revue" / "build" / "build_nuitka.py",
)

# Whole-program flags that would compile multiple modules into one unit and
# could reopen the cross-module folding question.
WHOLE_PROGRAM_FLAGS = ("--standalone", "--onefile", "--mode=standalone")


@pytest.mark.parametrize(
    "script", BUILD_NUITKA_SCRIPTS, ids=lambda p: p.parent.parent.name
)
def test_build_compiles_per_module(script: Path) -> None:
    """Each build script must invoke ``nuitka --module`` (per-file units)."""
    assert script.exists(), f"build script not found — test is mis-pathed: {script}"
    source = script.read_text(encoding="utf-8")
    assert '"--module"' in source, (
        f"{script.relative_to(REPO_ROOT)} no longer passes --module to nuitka. "
        f"Per-module compilation is what prevents cross-module folding of the "
        f"embedded JWT key (REVUE-334). If the build mode changed, re-evaluate "
        f"whether get_jwt_public_key() has become load-bearing."
    )


@pytest.mark.parametrize(
    "script", BUILD_NUITKA_SCRIPTS, ids=lambda p: p.parent.parent.name
)
def test_build_does_not_use_whole_program_mode(script: Path) -> None:
    """No whole-program flag may appear — that could reopen cross-module
    folding within a single compilation unit (REVUE-334)."""
    source = script.read_text(encoding="utf-8")
    offending = [flag for flag in WHOLE_PROGRAM_FLAGS if flag in source]
    assert offending == [], (
        f"{script.relative_to(REPO_ROOT)} introduces whole-program nuitka "
        f"flag(s) {offending}. This can make cross-module constant folding "
        f"possible, so the REVUE-334 no-folding guarantee no longer holds by "
        f"construction. Re-verify the JWT key binding before allowing this."
    )

"""Guard: JWT verify sites read the public key via the call-time accessor.

REVUE-334 AC1 regression guard. The embedded public key is consumed across
a wheel boundary: ``activate.py``/``validate.py`` ship in the ``revue`` wheel
and read the key from ``revue_core.security.jwt_keys`` in the separately
Nuitka-compiled ``revue_core`` wheel. The whole point of the
``get_jwt_public_key()`` accessor is that a cross-module *function call* is not
constant-folded by Nuitka, whereas a direct read of an imported constant can
be inlined into the caller's compiled body.

That protection holds ONLY while the verify sites:

1. import the *module* (``from revue_core.security import jwt_keys as _jwt_keys``)
   and never the *constant* (``from ...jwt_keys import JWT_PUBLIC_KEY_PEM``),
   which would bind the value at import time and defeat the accessor; and
2. actually call ``_jwt_keys.get_jwt_public_key()`` at each ``pyjwt.decode``
   site rather than reading ``_jwt_keys.JWT_PUBLIC_KEY_PEM`` directly.

A future refactor that reintroduced the ``from ... import`` form — e.g. by
copying the test suite's import pattern — would silently restore the
constant-folding hazard while every behavioural test stayed green (the value
is identical; only the *binding* changes). This static AST scan pins the
invariant so that regression fails at CI time instead of in a shipped binary.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGING_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PACKAGING_DIR / "src" / "revue_skill"

# The verify-site modules whose JWT key binding must stay accessor-routed.
VERIFY_SITE_MODULES = ("activate.py", "validate.py")

_KEY_CONSTANT = "JWT_PUBLIC_KEY_PEM"
_ACCESSOR = "get_jwt_public_key"


def _module_ast(filename: str) -> ast.Module:
    path = SRC_DIR / filename
    assert path.exists(), f"verify-site module not found — test is mis-pathed: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("filename", VERIFY_SITE_MODULES)
def test_verify_site_never_imports_the_key_constant_by_name(filename: str) -> None:
    """No ``from ...jwt_keys import JWT_PUBLIC_KEY_PEM`` at a verify site.

    The ``from ... import`` form binds the value at import time, which is
    exactly what lets Nuitka fold the key into the caller. Module-level
    ``import jwt_keys as _jwt_keys`` is the only permitted form.
    """
    tree = _module_ast(filename)

    offending = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.endswith("jwt_keys")
        and any(alias.name == _KEY_CONSTANT for alias in node.names)
    ]

    assert offending == [], (
        f"{filename} imports {_KEY_CONSTANT} via `from ...jwt_keys import` "
        f"(line {[n.lineno for n in offending]}). This binds the key at import "
        f"time and lets Nuitka constant-fold it into the compiled verify body, "
        f"defeating REVUE-334 AC1. Import the module and call "
        f"{_ACCESSOR}() instead."
    )


@pytest.mark.parametrize("filename", VERIFY_SITE_MODULES)
def test_verify_site_routes_key_through_the_accessor(filename: str) -> None:
    """Each verify site calls ``get_jwt_public_key()`` and never reads the
    constant attribute directly.

    Guards the other half of the invariant: even with the correct module
    import, a direct ``_jwt_keys.JWT_PUBLIC_KEY_PEM`` attribute read at the
    decode site would reintroduce the foldable reference.
    """
    tree = _module_ast(filename)

    accessor_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == _ACCESSOR
    ]
    direct_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == _KEY_CONSTANT
    ]

    assert accessor_calls, (
        f"{filename} never calls {_ACCESSOR}() — the verify site must read the "
        f"embedded key through the call-time accessor (REVUE-334 AC1)."
    )
    assert direct_reads == [], (
        f"{filename} reads {_KEY_CONSTANT} directly as an attribute "
        f"(line {[n.lineno for n in direct_reads]}). Route it through "
        f"{_ACCESSOR}() so Nuitka cannot fold the key into the compiled body."
    )

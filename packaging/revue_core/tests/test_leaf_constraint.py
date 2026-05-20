"""REVUE-310 — `revue_core` is the leaf of the package graph.

`revue_core` must NOT import from `revue_skill` (the Claude Code skill
wheel) or `revue_ci` (the CI/CLI entry point). It is the shared library
those packages consume; an upward reference would create a cycle and
break the atomic publish ordering (revue_core publishes first, before
either dependent exists on PyPI).

Run with the rest of the revue_core suite:
    pytest packaging/revue_core/tests/ --rootdir=packaging/revue_core
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import revue_core


SRC_ROOT = Path(revue_core.__file__).parent
FORBIDDEN_PACKAGES = ("revue_skill", "revue_ci")


def _python_modules() -> list[Path]:
    """Every .py under packaging/revue_core/src/revue_core/."""
    return [
        p
        for p in SRC_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_revue_core_has_no_upward_imports() -> None:
    """AST-walk every revue_core module and reject any import that names
    a forbidden top-level package.
    """
    offenders: list[tuple[Path, int, str]] = []

    for py_file in _python_modules():
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as exc:
            pytest.fail(f"Could not parse {py_file}: {exc}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top in FORBIDDEN_PACKAGES:
                        offenders.append((py_file, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if not node.module:
                    continue
                top = node.module.split(".", 1)[0]
                if top in FORBIDDEN_PACKAGES:
                    names = ", ".join(a.name for a in node.names)
                    offenders.append(
                        (py_file, node.lineno, f"from {node.module} import {names}")
                    )

    if offenders:
        lines = [
            f"  {p.relative_to(SRC_ROOT)}:{lineno}  {stmt}"
            for p, lineno, stmt in offenders
        ]
        pytest.fail(
            "revue_core is not a leaf package — found upward imports "
            f"into {FORBIDDEN_PACKAGES}:\n" + "\n".join(lines)
        )


def test_revue_core_modules_importable_without_dependents() -> None:
    """A representative cross-section of revue_core public modules must
    import without pulling in revue_skill or revue_ci as side-effects.
    The leaf invariant fails silently if a module triggers a lazy upward
    import only on use.
    """
    import importlib
    import sys

    # Snapshot which modules are already loaded so we can check delta.
    before = set(sys.modules)

    representative = [
        "revue_core.core.models",
        "revue_core.core.pipeline",
        "revue_core.comments.body_builder",
        "revue_core.comments.consolidator",
        "revue_core.core.agent_loader",
        "revue_core.core.diff_parser",
    ]
    for mod in representative:
        importlib.import_module(mod)

    after = set(sys.modules)
    pulled = after - before
    upward = sorted(
        m
        for m in pulled
        if m.split(".", 1)[0] in FORBIDDEN_PACKAGES
    )
    assert not upward, (
        f"revue_core modules pulled forbidden dependents into sys.modules: {upward}"
    )

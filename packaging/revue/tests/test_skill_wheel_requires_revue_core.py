"""REVUE-310 — the skill wheel partially vendors revue_core.

Hot-path / foundation modules (``position_adapter``, ``logging_channels``,
``display``, ``log``, ``finding_schema``, ``terminal_state``, positioning
adapters) ARE vendored into ``revue_skill/vendored/`` by
``tools/vendor_sources.py``.

The pipeline orchestration layer (``local_run.py``) is NOT vendored —
it keeps direct ``from revue_core.X`` imports inside function bodies
because vendoring the whole pipeline would multiply the wheel payload
for code that isn't a hot path.

That makes ``revue_core`` a load-bearing **runtime** dependency of the
skill wheel, not just a build-time source. This test locks both halves
of the contract so a future reader doesn't drop the dep on the (wrong)
assumption that the skill is fully self-contained.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PACKAGING_DIR = Path(__file__).resolve().parent.parent
PYPROJECT = PACKAGING_DIR / "pyproject.toml"
BUILD_WHEEL = PACKAGING_DIR / "build" / "build_wheel.py"
VENDORED_LOCAL_RUN = PACKAGING_DIR / "src" / "revue_skill" / "skill" / "local_run.py"


def _runtime_dependencies() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def _package_name(requirement: str) -> str:
    for sep in ("==", ">=", "<=", "~=", ">", "<", "!=", "["):
        idx = requirement.find(sep)
        if idx != -1:
            return requirement[:idx].strip().lower()
    return requirement.strip().lower()


def test_skill_wheel_declares_revue_core_as_runtime_dep() -> None:
    names = {_package_name(req) for req in _runtime_dependencies()}
    assert "revue_core" in names, (
        "packaging/revue/pyproject.toml must declare revue_core as a runtime "
        "dependency — the skill wheel's local_run.py has direct `from revue_core.X` "
        "imports that fail at runtime without it."
    )


def test_vendored_local_run_keeps_revue_core_runtime_imports() -> None:
    """Locks the partial-vendoring contract: ``local_run.py`` is intentionally
    NOT fully rewritten — most ``revue_core.*`` imports are kept and resolved
    at runtime against the installed ``revue_core`` package.

    If a future refactor vendors the rest of revue_core or strips local_run.py
    of these imports, drop the runtime dep in pyproject.toml in the same
    commit (and delete this test).
    """
    content = VENDORED_LOCAL_RUN.read_text(encoding="utf-8")

    # Strip TYPE_CHECKING blocks — those imports never run.
    content_without_type_checking = re.sub(
        r"if TYPE_CHECKING:.*?(?=\n\S|\Z)",
        "",
        content,
        flags=re.DOTALL,
    )

    revue_core_imports = re.findall(
        r"^\s*from revue_core\.[\w.]+ import",
        content_without_type_checking,
        flags=re.MULTILINE,
    )

    assert len(revue_core_imports) >= 5, (
        f"expected ≥5 runtime `from revue_core.X import` lines in vendored "
        f"local_run.py to justify the runtime dep; found {len(revue_core_imports)}. "
        "If the orchestration layer has been vendored, drop the revue_core "
        "runtime dep in packaging/revue/pyproject.toml and delete this test."
    )


def test_wheel_metadata_matches_pyproject_dependencies() -> None:
    """REVUE-278 — bridge the pyproject ↔ build_wheel.py gap.

    ``packaging/revue/build/build_wheel.py`` hand-rolls the wheel METADATA
    instead of reading pyproject.toml. That means a runtime dep declared in
    pyproject can silently disappear from the wheel's ``Requires-Dist`` lines —
    pip will then install the wheel without the dep and the first ``import
    revue_skill.validate`` crashes with ``ModuleNotFoundError`` on the
    customer's machine.

    Discovered when REVUE-278 added ``httpx`` (via the new ``validate.py`` +
    ``activate.py`` modules) to pyproject but the hand-rolled METADATA still
    only declared jsonschema + PyYAML. A freshly-built wheel installed cleanly
    but every gated command crashed on import.

    Lock the invariant: every package in pyproject's ``dependencies`` MUST
    appear as a ``Requires-Dist:`` line in build_wheel.py. Add new deps in
    both places.
    """
    declared = {_package_name(req) for req in _runtime_dependencies()}

    build_source = BUILD_WHEEL.read_text(encoding="utf-8")
    metadata_decls = re.findall(
        r'Requires-Dist:\s*([A-Za-z0-9_.\-\[\]]+)',
        build_source,
    )
    in_metadata = {_package_name(name) for name in metadata_decls}

    missing = declared - in_metadata
    assert not missing, (
        f"build_wheel.py METADATA is missing runtime deps from pyproject.toml: "
        f"{sorted(missing)}. The published wheel will install without them and "
        f"crash on import. Add `f\"Requires-Dist: <pkg>>=<version>\\n\"` lines "
        f"to packaging/revue/build/build_wheel.py:100-111 to match pyproject."
    )

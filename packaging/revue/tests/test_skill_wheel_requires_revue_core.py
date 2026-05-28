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


def test_build_wheel_reads_dependencies_from_pyproject() -> None:
    """REVUE-353 — the published wheel's Requires-Dist MUST come from
    pyproject.toml at build time, not from hardcoded strings.

    *Why this test exists.* REVUE-278 first surfaced the wheel-METADATA gap:
    pyproject declared revue_core + httpx but the hand-rolled METADATA in
    build_wheel.py only emitted jsonschema + PyYAML. REVUE-352 patched it by
    hardcoding the missing entries — and v0.24.1 shipped uninstallable.

    The interaction the hardcoded fix missed: REVUE-322's atomic-version
    invariant rewrites the ``revue_core~=`` pin in pyproject.toml via ``sed``
    at release time (bitbucket-pipelines.yml:428-429). If build_wheel.py
    hardcodes the pin, the sed bump never reaches the published wheel —
    pyproject says ``~=0.24.1`` but the METADATA says ``~=0.1.0`` and pip's
    resolver fails because no such revue_core exists on PyPI.

    The fix: ``build_wheel.read_dependencies()`` reads pyproject's
    ``[project.dependencies]`` directly. This test pins the contract.

    Failure modes this catches:
    1. Someone re-adds a hardcoded ``Requires-Dist:`` block (drift trap).
    2. ``read_dependencies()`` gets accidentally removed or stops reading
       from pyproject (e.g., switches to a stale snapshot file).
    3. The function silently returns an empty list, which would yield a
       wheel with zero declared deps.
    """
    import importlib.util
    from pathlib import Path

    # Import build_wheel.py as a module so we can call read_dependencies()
    # without running its __main__ block.
    spec = importlib.util.spec_from_file_location("build_wheel", BUILD_WHEEL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "read_dependencies"), (
        "build_wheel.py must expose `read_dependencies()` that reads "
        "[project.dependencies] from pyproject.toml. Without this function, "
        "the Tag Release sed-bump on `revue_core~=` cannot reach the wheel."
    )

    from_function = {_package_name(d) for d in mod.read_dependencies()}
    from_pyproject = {_package_name(d) for d in _runtime_dependencies()}

    assert from_function == from_pyproject, (
        f"build_wheel.read_dependencies() must return pyproject.toml's "
        f"[project.dependencies] verbatim. Got from function: "
        f"{sorted(from_function)}; from pyproject: {sorted(from_pyproject)}. "
        f"Diff: pyproject only={sorted(from_pyproject - from_function)}, "
        f"function only={sorted(from_function - from_pyproject)}."
    )

    # Guard against the regression that motivated this test: no hardcoded
    # `Requires-Dist:` literal for any runtime dep should appear in the
    # build_wheel.py source. The METADATA must be synthesised from
    # read_dependencies() output, never duplicated by hand.
    build_source = BUILD_WHEEL.read_text(encoding="utf-8")
    hardcoded_runtime = [
        name for name in (_package_name(d) for d in _runtime_dependencies())
        if re.search(rf'Requires-Dist:\s*{re.escape(name)}', build_source, re.IGNORECASE)
    ]
    assert not hardcoded_runtime, (
        f"build_wheel.py contains a hardcoded `Requires-Dist:` literal for "
        f"runtime dep(s): {hardcoded_runtime}. This duplicates pyproject.toml "
        f"and re-creates the REVUE-353 bug class — Tag Release's sed bump "
        f"of the version pin will silently fail to reach the wheel METADATA. "
        f"Generate the line from read_dependencies() instead."
    )

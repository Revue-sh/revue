"""REVUE-310 — revue_core is pure-Python; its dev extras must stay lean.

Nuitka build tooling (``nuitka``, ``ordered-set``, ``zstandard``) belongs to
the compiled wheels — ``revue-ci`` and the ``revue`` skill wheel — not to the
pure-Python leaf. Pulling them into ``revue_core[dev]`` makes a basic dev
install heavy and misleads contributors about what revue_core builds with.

The CI pipeline installs these explicitly into a dedicated build venv (see
``bitbucket-pipelines.yml``), so removing them here has no CI impact.
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.9/3.10 path
    import tomli as tomllib

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

NUITKA_BUILD_DEPS = {"nuitka", "ordered-set", "zstandard"}


def _dev_extras() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["dev"]


def _package_name(requirement: str) -> str:
    for sep in ("==", ">=", "<=", "~=", ">", "<", "!=", "["):
        idx = requirement.find(sep)
        if idx != -1:
            return requirement[:idx].strip().lower()
    return requirement.strip().lower()


def test_dev_extras_exclude_nuitka_build_tooling() -> None:
    names = {_package_name(req) for req in _dev_extras()}
    leaked = names & NUITKA_BUILD_DEPS
    assert not leaked, (
        f"revue_core[dev] must not include Nuitka build deps; found {sorted(leaked)}. "
        "These belong to revue-ci / revue skill wheel only."
    )

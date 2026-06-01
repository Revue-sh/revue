"""REVUE-369 F2: build_nuitka must clear stale intermediate output before compiling.

The Nuitka compile step glob-matches `.cpython-*.so` for each module in
`NUITKA_OUT_DIR`. If a prior build left a `.so` there with a different ABI
tag (e.g. cp314 from a previous Python toolchain on the same machine), the
glob will pick it up and the wheel will ship a mixed-ABI artefact.

The fix is to reset BOTH output directories at build start, not just
`COMPILED_DIR`.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
BUILD_DIR = PACKAGING_DIR / "build"
if str(BUILD_DIR) not in sys.path:
    sys.path.insert(0, str(BUILD_DIR))

import build_nuitka  # noqa: E402  (import after sys.path tweak)


def test_reset_build_dirs_removes_stale_nuitka_output(tmp_path, monkeypatch):
    # Arrange — point both build dirs at tmp paths and plant a stale .so
    nuitka_dir = tmp_path / "nuitka"
    compiled_dir = tmp_path / "compiled"
    nuitka_dir.mkdir()
    compiled_dir.mkdir()
    stale_so = nuitka_dir / "cache_paths.cpython-314-darwin.so"
    stale_so.write_bytes(b"stale-from-prior-abi")
    leftover_compiled = compiled_dir / "manifest.cpython-314-darwin.so"
    leftover_compiled.write_bytes(b"prior-compiled-output")

    monkeypatch.setattr(build_nuitka, "NUITKA_OUT_DIR", nuitka_dir)
    monkeypatch.setattr(build_nuitka, "COMPILED_DIR", compiled_dir)

    # Act
    build_nuitka._reset_build_dirs()

    # Assert — both stale artefacts are gone, dirs exist empty for the new build
    assert not stale_so.exists(), (
        "NUITKA_OUT_DIR must be cleared so stale-ABI .so files cannot leak "
        "into the new wheel via compile_module's glob (F2)"
    )
    assert not leftover_compiled.exists(), "COMPILED_DIR must be cleared at build start"
    assert nuitka_dir.is_dir() and not any(nuitka_dir.iterdir())
    assert compiled_dir.is_dir() and not any(compiled_dir.iterdir())


def test_reset_build_dirs_creates_dirs_if_missing(tmp_path, monkeypatch):
    # Arrange — directories do not yet exist
    nuitka_dir = tmp_path / "nuitka"
    compiled_dir = tmp_path / "compiled"
    assert not nuitka_dir.exists() and not compiled_dir.exists()

    monkeypatch.setattr(build_nuitka, "NUITKA_OUT_DIR", nuitka_dir)
    monkeypatch.setattr(build_nuitka, "COMPILED_DIR", compiled_dir)

    # Act
    build_nuitka._reset_build_dirs()

    # Assert — first-run case: dirs exist and are empty
    assert nuitka_dir.is_dir() and not any(nuitka_dir.iterdir())
    assert compiled_dir.is_dir() and not any(compiled_dir.iterdir())

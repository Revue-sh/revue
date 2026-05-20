#!/usr/bin/env python3
"""Compile the revue-ci entry point to a native .so / .pyd binary via Nuitka.

revue-ci is a thin wrapper around revue_core, so the only file we compile
is ``revue_ci/cli.py``. revue_core itself stays as a pure-Python source wheel
on PyPI; the compiled cli.py loads it at import time like any other dep.

Run order (from repo root):
    python packaging/revue-ci/build/build_nuitka.py
    python packaging/revue-ci/build/build_wheel.py

Output layout: packaging/revue-ci/dist/compiled/revue_ci/ with:
    - cli.cpython-*.so   — compiled CLI entry point
    - __init__.py        — plain; only carries package metadata
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
SRC_DIR = PACKAGING_DIR / "src" / "revue_ci"
NUITKA_OUT_DIR = PACKAGING_DIR / "dist" / "nuitka"
COMPILED_DIR = PACKAGING_DIR / "dist" / "compiled" / "revue_ci"

COMPILE_TARGETS = [SRC_DIR / "cli.py"]


def compile_module(py_file: Path) -> Path:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--module",
        str(py_file),
        f"--output-dir={NUITKA_OUT_DIR}",
        "--remove-output",
        "--no-pyi-file",
        "--assume-yes-for-downloads",
        "--no-progressbar",
    ]
    print(f"  Compiling {py_file.relative_to(PACKAGING_DIR)} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED: {py_file.name}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Nuitka failed for {py_file.name}")

    so_files = list(NUITKA_OUT_DIR.glob(f"{py_file.stem}.cpython-*.so"))
    if not so_files:
        so_files = list(NUITKA_OUT_DIR.glob(f"{py_file.stem}.cpython-*.pyd"))
    if not so_files:
        raise RuntimeError(f"no compiled output found for {py_file.name}")
    return so_files[0]


def main() -> None:
    print("=== revue-ci — Nuitka Build ===")
    print(f"Source:   {SRC_DIR}")
    print(f"Output:   {COMPILED_DIR}")
    print()

    if not SRC_DIR.is_dir():
        print(f"ERROR: {SRC_DIR} not found.", file=sys.stderr)
        sys.exit(1)

    if COMPILED_DIR.exists():
        shutil.rmtree(COMPILED_DIR)
    NUITKA_OUT_DIR.mkdir(parents=True, exist_ok=True)
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)

    for src_file in COMPILE_TARGETS:
        so_path = compile_module(src_file)
        rel = src_file.relative_to(SRC_DIR)
        dest = COMPILED_DIR / rel.parent / so_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(so_path, dest)
        print(f"  done: {rel.parent / so_path.name}")

    # __init__.py — plain Python (package marker)
    shutil.copy2(SRC_DIR / "__init__.py", COMPILED_DIR / "__init__.py")
    print("  __init__.py")

    print()
    print(f"Build complete: {COMPILED_DIR}")


if __name__ == "__main__":
    main()

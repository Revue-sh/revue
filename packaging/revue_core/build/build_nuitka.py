#!/usr/bin/env python3
"""Compile revue_core to native .so / .pyd binaries via Nuitka.

revue_core is the shared orchestration library that revue-ci and the revue
skill wheel both consume. The Python source is one of the project's IP
assets, so the PyPI artifact must ship as compiled binaries — never as
plain ``.py``.

Editable dev installs (``pip install -e packaging/revue_core/``) still use
the plain source under ``src/revue_core/``; only the wheel published to
PyPI is compiled.

Run order (from repo root):

    python packaging/revue_core/build/build_nuitka.py
    python packaging/revue_core/build/build_wheel.py

Output layout: ``packaging/revue_core/dist/compiled/revue_core/`` with:
    - <module>.cpython-*.so  — every compiled module, mirroring the source tree
    - __init__.py            — shipped as plain (Nuitka --module rejects these)
    - agents/*.{md,yaml,yml} — agent prompts (data files, by design)
    - teams/*.yml            — team configs (data files)
    - core/models_registry.yml + any other YAML / Markdown data files
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
SRC_DIR = PACKAGING_DIR / "src" / "revue_core"
NUITKA_OUT_DIR = PACKAGING_DIR / "dist" / "nuitka"
COMPILED_DIR = PACKAGING_DIR / "dist" / "compiled" / "revue_core"

# Filenames that we copy as-is rather than compile.
PLAIN_FILENAMES = {"__init__.py"}
# Filename suffixes that we treat as bundled data files (shipped verbatim).
DATA_SUFFIXES = {".md", ".yaml", ".yml", ".json"}
# Directories that should never enter the wheel.
EXCLUDE_DIRS = {"__pycache__"}
# Files that should never enter the wheel.
EXCLUDE_NAMES = {".DS_Store"}


def _iter_source_files() -> list[Path]:
    """Every file under SRC_DIR worth shipping, minus excluded paths."""
    out: list[Path] = []
    for path in SRC_DIR.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.name in EXCLUDE_NAMES:
            continue
        out.append(path)
    return sorted(out)


def compile_module(py_file: Path) -> Path:
    """Invoke Nuitka --module on ``py_file``; return the resulting binary path."""
    # Per-module output dirs prevent Nuitka build collisions when modules in
    # different subpackages share a basename (e.g. multiple `models.py`).
    rel = py_file.relative_to(SRC_DIR)
    module_out = NUITKA_OUT_DIR / rel.parent
    module_out.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--module",
        str(py_file),
        f"--output-dir={module_out}",
        "--remove-output",
        "--no-pyi-file",
        "--assume-yes-for-downloads",
        "--no-progressbar",
    ]
    print(f"  Compiling {rel} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED: {rel}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Nuitka failed for {rel}")

    so_files = list(module_out.glob(f"{py_file.stem}.cpython-*.so"))
    if not so_files:
        so_files = list(module_out.glob(f"{py_file.stem}.cpython-*.pyd"))
    if not so_files:
        raise RuntimeError(f"no compiled output found for {rel}")
    return so_files[0]


def main() -> None:
    print("=== revue_core — Nuitka Build ===")
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

    all_files = _iter_source_files()
    py_files = [
        f for f in all_files
        if f.suffix == ".py" and f.name not in PLAIN_FILENAMES
    ]
    plain_py = [f for f in all_files if f.suffix == ".py" and f.name in PLAIN_FILENAMES]
    data_files = [f for f in all_files if f.suffix in DATA_SUFFIXES]
    unhandled = [
        f for f in all_files
        if f not in py_files and f not in plain_py and f not in data_files
    ]
    if unhandled:
        print(f"WARNING: {len(unhandled)} file(s) not handled by build_nuitka.py:")
        for f in unhandled:
            print(f"  - {f.relative_to(SRC_DIR)}")

    # --- Step 1: Compile .py modules in parallel ---
    workers = min(os.cpu_count() or 2, 4)
    print(f"Compiling {len(py_files)} modules (workers={workers}) ...")

    failed = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(compile_module, f): f for f in py_files}
        for future in as_completed(futures):
            src_file = futures[future]
            rel = src_file.relative_to(SRC_DIR)
            try:
                so_path = future.result()
                dest = COMPILED_DIR / rel.parent / so_path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(so_path, dest)
                print(f"  done: {rel.parent / so_path.name}")
            except RuntimeError as exc:
                print(f"Build failed: {exc}", file=sys.stderr)
                failed = True

    if failed:
        sys.exit(1)

    # --- Step 2: Copy __init__.py files as plain ---
    print()
    print(f"Copying {len(plain_py)} __init__.py file(s) as plain ...")
    for src_file in plain_py:
        rel = src_file.relative_to(SRC_DIR)
        dest = COMPILED_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)
        print(f"  {rel}")

    # --- Step 3: Copy data files (.md / .yaml / .yml / .json) ---
    if data_files:
        print()
        print(f"Copying {len(data_files)} data file(s) ...")
        for src_file in data_files:
            rel = src_file.relative_to(SRC_DIR)
            dest = COMPILED_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            print(f"  {rel}")

    print()
    print(f"Build complete: {COMPILED_DIR}")


if __name__ == "__main__":
    main()

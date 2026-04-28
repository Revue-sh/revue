#!/usr/bin/env python3
"""Compile all revue/core/ modules to native .so binaries using Nuitka.

Output: dist/revue_compiled/revue/ with .so files for core/ and
plain Python/YAML for cli.py, __init__.py, agents/, teams/.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_REVUE = ROOT / "src" / "revue"
CORE_DIR = SRC_REVUE / "core"
OUTPUT_DIR = ROOT / "dist" / "nuitka"
COMPILED_DIR = ROOT / "dist" / "revue_compiled" / "revue"


def compile_module(py_file: Path) -> Path:
    """Compile a single .py file to a .so extension module with Nuitka."""
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--module",
        str(py_file),
        f"--output-dir={OUTPUT_DIR}",
        "--remove-output",
        "--no-pyi-file",
        "--assume-yes-for-downloads",  # suppress interactive prompts in CI
        "--no-progressbar",            # avoid TTY escape codes in CI logs
    ]
    print(f"  Compiling {py_file.name} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED: {py_file.name}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    # Nuitka produces <stem>.cpython-<tag>-<platform>.so in OUTPUT_DIR
    so_files = list(OUTPUT_DIR.glob(f"{py_file.stem}.cpython-*.so"))
    if not so_files:
        # On some platforms Nuitka may produce .pyd (Windows)
        so_files = list(OUTPUT_DIR.glob(f"{py_file.stem}.cpython-*.pyd"))
    if not so_files:
        print(f"  ERROR: no compiled output found for {py_file.name}", file=sys.stderr)
        sys.exit(1)
    return so_files[0]


def main():
    print("=== Revue Nuitka Build ===")
    print(f"Source:  {SRC_REVUE}")
    print(f"Output:  {COMPILED_DIR}")
    print()

    # Clean previous builds
    if COMPILED_DIR.exists():
        shutil.rmtree(COMPILED_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Compile all core/ .py modules ---
    core_dest = COMPILED_DIR / "core"
    core_dest.mkdir(parents=True, exist_ok=True)

    py_files = [f for f in sorted(CORE_DIR.glob("*.py")) if f.name != "__init__.py"]  # __init__.py copied separately — Nuitka --module rejects it
    if not py_files:
        print("ERROR: no .py files found in core/", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(py_files)} modules in core/")
    for py_file in py_files:
        so_path = compile_module(py_file)
        shutil.copy2(so_path, core_dest / so_path.name)

    # --- Step 2: Copy non-compiled files ---
    print()
    print("Copying non-compiled files ...")

    # cli.py
    shutil.copy2(SRC_REVUE / "cli.py", COMPILED_DIR / "cli.py")
    print("  cli.py")

    # __init__.py (package root)
    shutil.copy2(SRC_REVUE / "__init__.py", COMPILED_DIR / "__init__.py")
    print("  __init__.py")

    # core/__init__.py copied as plain Python — passing an __init__.py path to
    # Nuitka's --module flag is a fatal error; only the package directory is accepted.
    shutil.copy2(CORE_DIR / "__init__.py", core_dest / "__init__.py")
    print("  core/__init__.py")

    # agents/
    agents_src = SRC_REVUE / "agents"
    agents_dest = COMPILED_DIR / "agents"
    if agents_src.exists():
        shutil.copytree(agents_src, agents_dest, dirs_exist_ok=True)
        print(f"  agents/ ({len(list(agents_dest.iterdir()))} files)")

    # teams/
    teams_src = SRC_REVUE / "teams"
    teams_dest = COMPILED_DIR / "teams"
    if teams_src.exists():
        shutil.copytree(teams_src, teams_dest, dirs_exist_ok=True)
        print(f"  teams/ ({len(list(teams_dest.iterdir()))} files)")

    print()
    print(f"Build complete: {COMPILED_DIR}")


if __name__ == "__main__":
    main()

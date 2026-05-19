#!/usr/bin/env python3
"""Compile the revue skill orchestration to native .so / .pyd binaries via Nuitka.

Run order (from repo root):
    python packaging/revue/tools/vendor_sources.py --clean
    python packaging/revue/build/build_nuitka.py
    python packaging/revue/build/build_wheel.py

Output layout: packaging/revue/dist/compiled/revue_skill/ with:
    - *.so / *.pyd  — compiled orchestration modules (cli, install, manifest,
                       local_run, vendored/*.py)
    - __init__.py   — plain; only carries __version__
    - skill/        — SKILL.md + _revue/ agent prompts (plain text by design)
"""

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
SRC_DIR = PACKAGING_DIR / "src" / "revue_skill"
NUITKA_OUT_DIR = PACKAGING_DIR / "dist" / "nuitka"
COMPILED_DIR = PACKAGING_DIR / "dist" / "compiled" / "revue_skill"

# Python files that will be compiled to .so/.pyd.
# __init__.py is excluded — Nuitka --module rejects it as a target.
COMPILE_ROOTS = [
    SRC_DIR / "cli.py",
    SRC_DIR / "install.py",
    SRC_DIR / "manifest.py",
    SRC_DIR / "skill" / "local_run.py",
]

VENDORED_DIR = SRC_DIR / "vendored"


def compile_module(py_file: Path, extra_include: list[str] | None = None) -> Path:
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
    if extra_include:
        cmd.extend(extra_include)

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
    print("=== Revue Skill — Nuitka Build ===")
    print(f"Source:   {SRC_DIR}")
    print(f"Output:   {COMPILED_DIR}")
    print()

    # Vendor step must have run already — fail fast if not.
    if not SRC_DIR.is_dir():
        print(
            f"ERROR: {SRC_DIR} not found — run vendor_sources.py --clean first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Clean previous compiled output.
    if COMPILED_DIR.exists():
        shutil.rmtree(COMPILED_DIR)
    NUITKA_OUT_DIR.mkdir(parents=True, exist_ok=True)
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Compile top-level orchestration modules ---
    print(f"Compiling top-level modules ...")
    failed = False
    all_sources = COMPILE_ROOTS[:]

    # Collect vendored/*.py (excluding __init__.py — handled separately)
    vendored_py = [
        f for f in sorted(VENDORED_DIR.glob("*.py")) if f.name != "__init__.py"
    ]
    # Collect vendored/positioning_adapters/*.py (excluding __init__.py)
    pos_adapters_dir = VENDORED_DIR / "positioning_adapters"
    if pos_adapters_dir.is_dir():
        vendored_py += [
            f for f in sorted(pos_adapters_dir.glob("*.py")) if f.name != "__init__.py"
        ]

    all_sources.extend(vendored_py)

    workers = min(os.cpu_count() or 2, 4)
    print(f"Compiling {len(all_sources)} modules (workers={workers}) ...")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(compile_module, f): f for f in all_sources}
        for future in as_completed(futures):
            src_file = futures[future]
            try:
                so_path = future.result()
                # Mirror directory structure under COMPILED_DIR
                rel = src_file.relative_to(SRC_DIR)
                dest = COMPILED_DIR / rel.parent / so_path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(so_path, dest)
                print(f"  done: {rel.parent / so_path.name}")
            except RuntimeError as exc:
                print(f"Build failed: {exc}", file=sys.stderr)
                failed = True

    if failed:
        sys.exit(1)

    # --- Step 2: Copy non-compiled files ---
    print()
    print("Copying non-compiled files ...")

    # __init__.py — plain Python (only carries __version__)
    shutil.copy2(SRC_DIR / "__init__.py", COMPILED_DIR / "__init__.py")
    print("  __init__.py")

    # vendored/__init__.py — docstring-only, safe as plain Python
    vendored_init_src = VENDORED_DIR / "__init__.py"
    if vendored_init_src.is_file():
        (COMPILED_DIR / "vendored").mkdir(parents=True, exist_ok=True)
        shutil.copy2(vendored_init_src, COMPILED_DIR / "vendored" / "__init__.py")
        print("  vendored/__init__.py")

    # vendored/positioning_adapters/__init__.py
    pos_init_src = VENDORED_DIR / "positioning_adapters" / "__init__.py"
    if pos_init_src.is_file():
        (COMPILED_DIR / "vendored" / "positioning_adapters").mkdir(parents=True, exist_ok=True)
        shutil.copy2(pos_init_src, COMPILED_DIR / "vendored" / "positioning_adapters" / "__init__.py")
        print("  vendored/positioning_adapters/__init__.py")

    # skill/ — SKILL.md + _revue/ agent prompts (intentionally plain text)
    skill_src = SRC_DIR / "skill"
    skill_dst = COMPILED_DIR / "skill"
    if skill_src.is_dir():
        shutil.copytree(
            skill_src,
            skill_dst,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.py", "__pycache__"),
        )
        data_files = sum(1 for _ in skill_dst.rglob("*") if _.is_file())
        print(f"  skill/ ({data_files} data files)")

    print()
    print(f"Build complete: {COMPILED_DIR}")


if __name__ == "__main__":
    main()

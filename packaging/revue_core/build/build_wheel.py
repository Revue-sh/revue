#!/usr/bin/env python3
"""Assemble a platform-specific .whl from the Nuitka-compiled revue_core output.

Reads version + runtime deps from ``packaging/revue_core/pyproject.toml``,
packages ``packaging/revue_core/dist/compiled/`` into a wheel, and writes
it to ``packaging/revue_core/dist/wheels/``.

revue_core is a library — the wheel has no entry_points.txt / console_scripts.

Run after ``build_nuitka.py``::

    python packaging/revue_core/build/build_wheel.py
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import platform
import re
import struct
import sys
import zipfile
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
COMPILED_DIR = PACKAGING_DIR / "dist" / "compiled"
WHEELS_DIR = PACKAGING_DIR / "dist" / "wheels"
PYPROJECT = PACKAGING_DIR / "pyproject.toml"


def read_version() -> str:
    text = PYPROJECT.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        print("ERROR: could not parse version from pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def read_runtime_dependencies() -> list[str]:
    """Return the contents of the ``[project] dependencies`` table.

    Anchored at the ``dependencies = [`` block so optional-dependencies and
    comments elsewhere in the file can't pollute the result.
    """
    text = PYPROJECT.read_text()
    deps_block = re.search(
        r'^dependencies\s*=\s*\[(.*?)^\]',
        text,
        re.DOTALL | re.MULTILINE,
    )
    if not deps_block:
        print(
            "ERROR: could not find `dependencies = [...]` in pyproject.toml",
            file=sys.stderr,
        )
        sys.exit(1)
    return re.findall(r'"([^"]+)"', deps_block.group(1))


def read_requires_python() -> str:
    text = PYPROJECT.read_text()
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else ">=3.9"


def get_python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def get_platform_tag() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        arch = machine  # x86_64, aarch64
        return f"manylinux_2_17_{arch}"
    elif system == "darwin":
        if machine == "arm64":
            return "macosx_14_0_arm64"
        return "macosx_10_9_x86_64"
    elif system == "windows":
        if struct.calcsize("P") * 8 == 64:
            return "win_amd64"
        return "win32"
    else:
        return f"{system}_{machine}"


def sha256_digest(data: bytes) -> str:
    h = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(h).rstrip(b"=").decode()


def build_wheel() -> None:
    version = read_version()
    deps = read_runtime_dependencies()
    requires_python = read_requires_python()
    py_tag = get_python_tag()
    plat_tag = get_platform_tag()
    wheel_name = f"revue_core-{version}-{py_tag}-{py_tag}-{plat_tag}.whl"

    print(f"=== Building wheel: {wheel_name} ===")
    print(f"Version:        {version}")
    print(f"Python:         {py_tag}")
    print(f"Platform:       {plat_tag}")
    print(f"Requires-Python:{requires_python}")
    print(f"Deps ({len(deps)}):")
    for dep in deps:
        print(f"  - {dep}")
    print()

    if not COMPILED_DIR.exists():
        print("ERROR: compiled output not found at", COMPILED_DIR, file=sys.stderr)
        print("Run build_nuitka.py first.", file=sys.stderr)
        sys.exit(1)

    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    wheel_path = WHEELS_DIR / wheel_name

    dist_info_dir = f"revue_core-{version}.dist-info"
    record_entries: list[tuple[str, str, int]] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        for file_path in sorted(COMPILED_DIR.rglob("*")):
            if file_path.is_file():
                arc_name = str(file_path.relative_to(COMPILED_DIR))
                data = file_path.read_bytes()
                whl.writestr(arc_name, data)
                record_entries.append((arc_name, sha256_digest(data), len(data)))

        metadata_lines = [
            "Metadata-Version: 2.1",
            "Name: revue_core",
            f"Version: {version}",
            "Summary: Shared orchestration logic for Revue multi-agent code review pipeline — CLI and CI integrations.",
            "Home-page: https://revue.sh",
            "Author-email: team@revue.sh",
            "License: Apache-2.0",
            f"Requires-Python: {requires_python}",
        ]
        for dep in deps:
            metadata_lines.append(f"Requires-Dist: {dep}")
        metadata = "\n".join(metadata_lines) + "\n"
        arc = f"{dist_info_dir}/METADATA"
        data = metadata.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        wheel_meta = (
            f"Wheel-Version: 1.0\n"
            f"Generator: revue_core-build-wheel\n"
            f"Root-Is-Purelib: false\n"
            f"Tag: {py_tag}-{py_tag}-{plat_tag}\n"
        )
        arc = f"{dist_info_dir}/WHEEL"
        data = wheel_meta.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        arc = f"{dist_info_dir}/top_level.txt"
        data = b"revue_core\n"
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # NB: revue_core is a library — no entry_points.txt.

        record_buf = io.StringIO()
        writer = csv.writer(record_buf)
        for name, digest, size in record_entries:
            writer.writerow([name, digest, str(size)])
        writer.writerow([f"{dist_info_dir}/RECORD", "", ""])
        arc = f"{dist_info_dir}/RECORD"
        whl.writestr(arc, record_buf.getvalue())

    print(f"Wheel written: {wheel_path}")
    print(f"Size: {wheel_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    build_wheel()

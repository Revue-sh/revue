#!/usr/bin/env python3
"""Assemble a platform-specific .whl from the Nuitka-compiled revue-ci output.

Reads version from packaging/revue-ci/pyproject.toml, packages
packaging/revue-ci/dist/compiled/ into a wheel, and writes it to
packaging/revue-ci/dist/wheels/.

Run after build_nuitka.py:
    python packaging/revue-ci/build/build_wheel.py
"""

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


def read_version() -> str:
    pyproject = PACKAGING_DIR / "pyproject.toml"
    text = pyproject.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        print("ERROR: could not parse version from pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def read_revue_core_constraint() -> str:
    """Extract the revue_core version constraint from the
    ``[project] dependencies`` table in pyproject.toml so the Nuitka-compiled
    wheel pins the same version range as the source build.

    Anchored at the ``dependencies = [`` block to avoid matching strings in
    ``[project.optional-dependencies]`` or comments.
    """
    pyproject = PACKAGING_DIR / "pyproject.toml"
    text = pyproject.read_text()
    deps_block = re.search(
        r'^dependencies\s*=\s*\[(.*?)^\]',
        text,
        re.DOTALL | re.MULTILINE,
    )
    if not deps_block:
        print("ERROR: could not find `dependencies = [...]` in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    match = re.search(r'"(revue_core[^"]+)"', deps_block.group(1))
    if not match:
        print("ERROR: could not parse revue_core constraint from dependencies", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


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
    revue_core_constraint = read_revue_core_constraint()
    py_tag = get_python_tag()
    plat_tag = get_platform_tag()
    wheel_name = f"revue_ci-{version}-{py_tag}-{py_tag}-{plat_tag}.whl"

    print(f"=== Building wheel: {wheel_name} ===")
    print(f"Version:  {version}")
    print(f"Python:   {py_tag}")
    print(f"Platform: {plat_tag}")
    print(f"Depends:  {revue_core_constraint}")
    print()

    if not COMPILED_DIR.exists():
        print("ERROR: compiled output not found at", COMPILED_DIR, file=sys.stderr)
        print("Run build_nuitka.py first.", file=sys.stderr)
        sys.exit(1)

    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    wheel_path = WHEELS_DIR / wheel_name

    dist_info_dir = f"revue_ci-{version}.dist-info"
    record_entries: list[tuple[str, str, int]] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        for file_path in sorted(COMPILED_DIR.rglob("*")):
            if file_path.is_file():
                arc_name = str(file_path.relative_to(COMPILED_DIR))
                data = file_path.read_bytes()
                whl.writestr(arc_name, data)
                record_entries.append((arc_name, sha256_digest(data), len(data)))

        metadata = (
            f"Metadata-Version: 2.1\n"
            f"Name: revue-ci\n"
            f"Version: {version}\n"
            f"Summary: Revue CI/CLI entry point — multi-agent code review for CI runners.\n"
            f"Home-page: https://revue.sh\n"
            f"Author-email: team@revue.sh\n"
            f"License: Apache-2.0\n"
            f"Requires-Python: >=3.9\n"
            f"Requires-Dist: {revue_core_constraint}\n"
        )
        arc = f"{dist_info_dir}/METADATA"
        data = metadata.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        wheel_meta = (
            f"Wheel-Version: 1.0\n"
            f"Generator: revue-ci-build-wheel\n"
            f"Root-Is-Purelib: false\n"
            f"Tag: {py_tag}-{py_tag}-{plat_tag}\n"
        )
        arc = f"{dist_info_dir}/WHEEL"
        data = wheel_meta.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        arc = f"{dist_info_dir}/top_level.txt"
        data = b"revue_ci\n"
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        entry_points = "[console_scripts]\nrevue-ci = revue_ci.cli:main\n"
        arc = f"{dist_info_dir}/entry_points.txt"
        data = entry_points.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

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

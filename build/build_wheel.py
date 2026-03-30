#!/usr/bin/env python3
"""Assemble a platform-specific .whl from the Nuitka-compiled output.

Reads version from src/pyproject.toml, packages dist/revue_compiled/
into a wheel, and writes it to dist/wheels/.
"""

import hashlib
import base64
import csv
import io
import os
import platform
import re
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPILED_DIR = ROOT / "dist" / "revue_compiled"
WHEELS_DIR = ROOT / "dist" / "wheels"


def read_version() -> str:
    """Extract version from src/pyproject.toml."""
    pyproject = ROOT / "src" / "pyproject.toml"
    text = pyproject.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        print("ERROR: could not parse version from pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def get_python_tag() -> str:
    """Return cpXYZ tag for current Python, e.g. cp312."""
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def get_platform_tag() -> str:
    """Return PEP 425 platform tag for current system."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        # Use manylinux for broad compatibility
        arch = machine  # x86_64, aarch64
        return f"manylinux_2_17_{arch}"
    elif system == "darwin":
        # macOS — use 14.0 as minimum for ARM64
        if machine == "arm64":
            return "macosx_14_0_arm64"
        return "macosx_10_9_x86_64"
    elif system == "windows":
        if struct.calcsize("P") * 8 == 64:
            return "win_amd64"
        return "win32"
    else:
        # Fallback
        return f"{system}_{machine}"


def sha256_digest(data: bytes) -> str:
    """Return urlsafe base64 sha256 digest (no padding) for RECORD."""
    h = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(h).rstrip(b"=").decode()


def build_wheel():
    version = read_version()
    py_tag = get_python_tag()
    plat_tag = get_platform_tag()
    wheel_name = f"revue-{version}-{py_tag}-{py_tag}-{plat_tag}.whl"

    print(f"=== Building wheel: {wheel_name} ===")
    print(f"Version:  {version}")
    print(f"Python:   {py_tag}")
    print(f"Platform: {plat_tag}")
    print()

    if not COMPILED_DIR.exists():
        print("ERROR: compiled output not found at", COMPILED_DIR, file=sys.stderr)
        print("Run build_nuitka.py first.", file=sys.stderr)
        sys.exit(1)

    WHEELS_DIR.mkdir(parents=True, exist_ok=True)
    wheel_path = WHEELS_DIR / wheel_name

    dist_info_dir = f"revue-{version}.dist-info"

    # Collect all files to package
    record_entries = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        # Add all compiled/copied files under revue/
        for file_path in sorted(COMPILED_DIR.rglob("*")):
            if file_path.is_file():
                arc_name = f"revue/{file_path.relative_to(COMPILED_DIR)}"
                data = file_path.read_bytes()
                whl.writestr(arc_name, data)
                record_entries.append((arc_name, sha256_digest(data), len(data)))

        # --- dist-info metadata ---

        # METADATA
        metadata = (
            f"Metadata-Version: 2.1\n"
            f"Name: revue\n"
            f"Version: {version}\n"
            f"Summary: AI-powered multi-agent code review\n"
            f"Requires-Python: >=3.12\n"
            f"Requires-Dist: openai>=1.0\n"
            f"Requires-Dist: anthropic>=0.20\n"
            f"Requires-Dist: httpx>=0.27\n"
            f"Requires-Dist: pyyaml>=6.0\n"
        )
        arc = f"{dist_info_dir}/METADATA"
        data = metadata.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # WHEEL
        wheel_meta = (
            f"Wheel-Version: 1.0\n"
            f"Generator: revue-build-wheel\n"
            f"Root-Is-Purelib: false\n"
            f"Tag: {py_tag}-{py_tag}-{plat_tag}\n"
        )
        arc = f"{dist_info_dir}/WHEEL"
        data = wheel_meta.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # top_level.txt
        arc = f"{dist_info_dir}/top_level.txt"
        data = b"revue\n"
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # entry_points.txt
        entry_points = "[console_scripts]\nrevue = revue.cli:main\n"
        arc = f"{dist_info_dir}/entry_points.txt"
        data = entry_points.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # RECORD (must be last, references itself without hash)
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

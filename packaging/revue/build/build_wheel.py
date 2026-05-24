#!/usr/bin/env python3
"""Assemble a platform-specific .whl from the Nuitka-compiled revue skill output.

Reads version from packaging/revue/pyproject.toml, packages
packaging/revue/dist/compiled/ into a wheel, and writes it to
packaging/revue/dist/wheels/.

Run after build_nuitka.py:
    python packaging/revue/build/build_wheel.py
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
    record_entries: list[tuple[str, str, int]] = []

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as whl:
        # All compiled/data files — paths are relative to COMPILED_DIR and
        # already include revue_skill/ prefix.
        for file_path in sorted(COMPILED_DIR.rglob("*")):
            if file_path.is_file():
                arc_name = str(file_path.relative_to(COMPILED_DIR))
                data = file_path.read_bytes()
                whl.writestr(arc_name, data)
                record_entries.append((arc_name, sha256_digest(data), len(data)))

        # METADATA
        metadata = (
            f"Metadata-Version: 2.1\n"
            f"Name: revue\n"
            f"Version: {version}\n"
            f"Summary: Run Revue multi-agent code review locally via a Claude Code skill\n"
            f"Home-page: https://revue.sh\n"
            f"Author-email: team@revue.sh\n"
            f"License: Apache-2.0\n"
            f"Requires-Python: >=3.12\n"
            # IP-PROTECTION INVARIANT: this list MUST stay in sync with the
            # `dependencies` array in packaging/revue/pyproject.toml. The wheel
            # METADATA is what pip resolves at install time — if a runtime dep
            # is missing here, the wheel imports crash on a fresh customer
            # install. `test_wheel_metadata_matches_pyproject_dependencies`
            # enforces parity. When adding a new licence/network-touching
            # module that pulls in a new dep, add it BOTH here and in
            # pyproject.toml.
            f"Requires-Dist: revue_core~=0.1.0\n"
            f"Requires-Dist: jsonschema>=4.21\n"
            f"Requires-Dist: PyYAML>=6.0\n"
            f"Requires-Dist: httpx>=0.27\n"
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
        data = b"revue_skill\n"
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # entry_points.txt
        entry_points = "[console_scripts]\nrevue = revue_skill.cli:main\n"
        arc = f"{dist_info_dir}/entry_points.txt"
        data = entry_points.encode()
        whl.writestr(arc, data)
        record_entries.append((arc, sha256_digest(data), len(data)))

        # RECORD (must be last)
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

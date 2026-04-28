"""Guard tests — CI flags in build/build_nuitka.py must never be removed.

These tests intercept the subprocess call in compile_module() and assert
that the two flags required for non-interactive CI are always present:
  --assume-yes-for-downloads  suppresses interactive prompts (hang prevention)
  --no-progressbar            prevents ANSI escape codes in CI log output
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BUILD_SCRIPT = Path(__file__).resolve().parents[4] / "build" / "build_nuitka.py"


def _load_build_nuitka():
    spec = importlib.util.spec_from_file_location("build_nuitka", _BUILD_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture_compile_cmd(tmp_path: Path) -> list[str]:
    """Load build_nuitka, run compile_module() with mocked subprocess, return the captured cmd."""
    mod = _load_build_nuitka()

    fake_py = tmp_path / "fake_module.py"
    fake_py.write_text("x = 1")

    # Nuitka outputs <stem>.cpython-<maj><min>-<platform>.so into OUTPUT_DIR
    tag = f"{sys.version_info.major}{sys.version_info.minor}"
    fake_so = tmp_path / f"fake_module.cpython-{tag}-linux-gnu.so"
    fake_so.touch()

    captured: list[str] = []

    def _mock_run(cmd, **kwargs):
        captured.extend(cmd)
        return MagicMock(returncode=0)

    original_output_dir = mod.OUTPUT_DIR
    mod.OUTPUT_DIR = tmp_path
    try:
        with patch("subprocess.run", side_effect=_mock_run):
            mod.compile_module(fake_py)
    finally:
        mod.OUTPUT_DIR = original_output_dir

    return captured


class TestNuitkaCIFlags:
    def test_assume_yes_for_downloads_present(self, tmp_path):
        """Removal causes CI to hang forever waiting for user confirmation."""
        cmd = _capture_compile_cmd(tmp_path)
        assert "--assume-yes-for-downloads" in cmd, (
            "--assume-yes-for-downloads removed from build/build_nuitka.py — "
            "Nuitka will prompt for confirmation in CI and hang indefinitely (see REVUE-191)"
        )

    def test_no_progressbar_present(self, tmp_path):
        """Removal causes ANSI escape codes to corrupt CI log output."""
        cmd = _capture_compile_cmd(tmp_path)
        assert "--no-progressbar" in cmd, (
            "--no-progressbar removed from build/build_nuitka.py — "
            "Nuitka progress bar emits ANSI escape codes that corrupt CI logs (see REVUE-191)"
        )

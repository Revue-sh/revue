"""Guard test — build/build_wheel.py must not double-nest the revue package.

The wheel assembler reads from dist/revue_compiled/ (which already contains
a revue/ subdirectory produced by build_nuitka.py).  Prefixing archive paths
with revue/ a second time produces revue/revue/cli.py — unreachable from
the entry point revue.cli:main and triggers ModuleNotFoundError at runtime.

Regression guard for REVUE-196.
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

_BUILD_WHEEL_SCRIPT = Path(__file__).resolve().parents[4] / "build" / "build_wheel.py"


def _load_build_wheel():
    spec = importlib.util.spec_from_file_location("build_wheel", _BUILD_WHEEL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_test_wheel(tmp_path: Path) -> Path:
    """Run build_wheel() against a minimal fake compiled tree and return the .whl path."""
    mod = _load_build_wheel()

    # Replicate what build_nuitka.py produces:
    # dist/revue_compiled/revue/cli.py
    # dist/revue_compiled/revue/__init__.py
    # dist/revue_compiled/revue/core/__init__.py
    compiled_dir = tmp_path / "dist" / "revue_compiled"
    revue_dir = compiled_dir / "revue"
    core_dir = revue_dir / "core"
    core_dir.mkdir(parents=True)

    (revue_dir / "cli.py").write_text("def main(): pass")
    (revue_dir / "__init__.py").write_text("")
    (core_dir / "__init__.py").write_text("")
    (core_dir / "pipeline.py").write_text("# stub")

    wheels_dir = tmp_path / "dist" / "wheels"

    # Patch module-level constants so the function uses our tmp tree
    original_compiled = mod.COMPILED_DIR
    original_wheels = mod.WHEELS_DIR
    original_root = mod.ROOT
    mod.COMPILED_DIR = compiled_dir
    mod.WHEELS_DIR = wheels_dir
    mod.ROOT = tmp_path

    # Patch pyproject.toml path to the real one (read_version() uses mod.ROOT)
    # by temporarily symlinking or just monkeypatching read_version
    original_read_version = mod.read_version
    mod.read_version = lambda: "99.0.0"

    try:
        mod.build_wheel()
    finally:
        mod.COMPILED_DIR = original_compiled
        mod.WHEELS_DIR = original_wheels
        mod.ROOT = original_root
        mod.read_version = original_read_version

    whl_files = list(wheels_dir.glob("*.whl"))
    assert whl_files, "build_wheel() produced no .whl file"
    return whl_files[0]


class TestWheelPackageStructure:
    def test_cli_at_correct_path(self, tmp_path):
        """revue/cli.py must be at revue/cli.py, not revue/revue/cli.py."""
        whl = _build_test_wheel(tmp_path)
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
        assert "revue/cli.py" in names, (
            f"revue/cli.py missing from wheel — entry point revue.cli:main unreachable. "
            f"Wheel contents: {[n for n in names if 'cli' in n]}"
        )

    def test_no_double_nesting(self, tmp_path):
        """revue/revue/cli.py must NOT exist — that was the REVUE-196 double-nesting bug."""
        whl = _build_test_wheel(tmp_path)
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
        double_nested = [n for n in names if n.startswith("revue/revue/")]
        assert not double_nested, (
            f"Wheel contains double-nested paths (revue/revue/…): {double_nested}. "
            "build_wheel.py is prepending 'revue/' to paths that already start with 'revue/'. "
            "See REVUE-196."
        )

    def test_init_at_correct_path(self, tmp_path):
        """revue/__init__.py must be at revue/__init__.py."""
        whl = _build_test_wheel(tmp_path)
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
        assert "revue/__init__.py" in names, (
            f"revue/__init__.py missing from wheel. "
            f"Wheel top-level paths: {sorted(set(n.split('/')[0] for n in names))}"
        )

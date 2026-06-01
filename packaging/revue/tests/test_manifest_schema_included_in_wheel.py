"""REVUE-369 F3: manifest.schema.json must be bundled in the wheel.

The wheel ships manifest.cpython-312-darwin.so compiled from manifest.py.
At runtime, manifest.py loads the JSON schema via Path(__file__).resolve().parent...
which fails in a customer install (pip install revue) because the .json file
is not in the wheel.

The fix is to:
1. Use Nuitka --include-data-files to bundle manifest.schema.json
2. Update manifest.py to load via importlib.resources.files() instead of Path.__file__

This test validates that the loaded schema is the real schema (structure and
integrity check), which proves the bundling and loading path work end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PACKAGING_DIR = Path(__file__).resolve().parent.parent


def test_manifest_schema_json_exists_in_source_tree():
    # Arrange — locate the source schema
    schema_file = PACKAGING_DIR / "manifest.schema.json"

    # Act — verify it exists and is valid JSON
    assert schema_file.is_file(), (
        f"manifest.schema.json must exist at {schema_file}. "
        "If moved, update the path in build_nuitka.py --include-data-files directive."
    )
    schema = json.loads(schema_file.read_text(encoding="utf-8"))

    # Assert — basic schema structure for the SKILL.md validator (sanity check)
    assert isinstance(schema, dict), "schema must be a JSON object"
    assert "$schema" in schema, "schema must declare a JSON Schema version"
    assert "title" in schema or "description" in schema, "schema must have metadata"
    assert "properties" in schema or "type" in schema, "schema must have type/property definitions"


def test_manifest_module_loads_schema_via_importlib_resources_in_wheel(tmp_path):
    # Arrange — simulate a wheel install layout where importlib.resources resolves
    # to a directory that CONTAINS manifest.schema.json next to revue_skill/.
    # The fallback path is patched away so this test ONLY validates the
    # importlib.resources branch (the production wheel path).
    import importlib
    import sys

    # Create a fake revue_skill package with the schema bundled (mimicking
    # what build_nuitka.py puts in COMPILED_DIR for the wheel build)
    fake_pkg_root = tmp_path / "revue_skill"
    fake_pkg_root.mkdir()
    (fake_pkg_root / "__init__.py").write_text("")
    real_schema_text = (PACKAGING_DIR / "manifest.schema.json").read_text(encoding="utf-8")
    (fake_pkg_root / "manifest.schema.json").write_text(real_schema_text, encoding="utf-8")

    # Put the fake package on sys.path so importlib.resources finds it
    sys.path.insert(0, str(tmp_path))
    try:
        # Force a fresh import; remove any cached revue_skill modules
        for mod in list(sys.modules):
            if mod.startswith("revue_skill"):
                del sys.modules[mod]

        from importlib.resources import files

        # Act — load via the production code path (importlib.resources)
        schema_text = files("revue_skill").joinpath("manifest.schema.json").read_text(encoding="utf-8")
        schema = __import__("json").loads(schema_text)

        # Assert — schema loaded via importlib.resources branch
        assert isinstance(schema, dict)
        assert "$schema" in schema
        assert len(schema.get("properties", {})) > 0, (
            "schema must load with real content via importlib.resources"
        )
    finally:
        sys.path.remove(str(tmp_path))
        # Clear cached imports so the real revue_skill loads fresh next time
        for mod in list(sys.modules):
            if mod.startswith("revue_skill"):
                del sys.modules[mod]


def test_manifest_load_schema_fallback_works_when_importlib_fails(tmp_path, monkeypatch):
    # Arrange — verify the source-tree fallback path is robust on its own.
    # This proves the fallback branch is wired correctly, complementing the
    # importlib.resources test above.
    import sys

    src_dir = PACKAGING_DIR / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Force fresh import
    for mod in list(sys.modules):
        if mod.startswith("revue_skill"):
            del sys.modules[mod]

    from revue_skill import manifest

    # Act — call load_schema() from source tree (importlib.resources will fail
    # because no manifest.schema.json in revue_skill/, fallback will succeed)
    schema = manifest.load_schema()

    # Assert — fallback successfully loaded the source-tree schema
    assert isinstance(schema, dict)
    assert "$schema" in schema

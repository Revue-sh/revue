"""Test: manifest.example.json conforms to the documented JSON Schema (AC5)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from revue_skill.manifest import MANIFEST_SCHEMA, ManifestError, validate

PACKAGING_DIR = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((PACKAGING_DIR / "manifest.example.json").read_text())


def test_example_manifest_validates() -> None:
    validate(EXAMPLE)


def test_schema_declares_required_fields() -> None:
    assert MANIFEST_SCHEMA["required"] == [
        "schema_version",
        "current_version",
        "released_at",
        "artefacts",
    ]


@pytest.mark.parametrize(
    "mutator,expected_message_fragment",
    [
        (lambda m: m.pop("current_version"), "current_version"),
        (lambda m: m.__setitem__("current_version", "not-semver"), "current_version"),
        (lambda m: m.__setitem__("schema_version", 999), "schema_version"),
        (lambda m: m["artefacts"]["wheel"].pop("sha256"), "sha256"),
        (lambda m: m["artefacts"]["wheel"].__setitem__("sha256", "tooShort"), "sha256"),
    ],
)
def test_rejects_malformed_manifest(mutator, expected_message_fragment: str) -> None:
    bad = copy.deepcopy(EXAMPLE)
    mutator(bad)
    with pytest.raises(ManifestError) as exc_info:
        validate(bad)
    assert expected_message_fragment in str(exc_info.value)


def test_validate_rejects_extra_top_level_keys() -> None:
    bad = copy.deepcopy(EXAMPLE)
    bad["surprise"] = True
    with pytest.raises(ManifestError):
        validate(bad)


# ---------------------------------------------------------------------------
# REVUE-310 — optional revue_core_min_version
# ---------------------------------------------------------------------------


def test_revue_core_min_version_is_optional() -> None:
    """A manifest without revue_core_min_version still validates — the field
    is purely additive."""
    sample = copy.deepcopy(EXAMPLE)
    sample.pop("revue_core_min_version", None)
    validate(sample)


def test_revue_core_min_version_accepts_semver() -> None:
    sample = copy.deepcopy(EXAMPLE)
    sample["revue_core_min_version"] = "0.1.0"
    validate(sample)


@pytest.mark.parametrize(
    "bad_value",
    ["1", "1.0", "1.0.0.0", "1.0.0-rc1", "v1.0.0", "not-a-version"],
)
def test_revue_core_min_version_rejects_non_semver(bad_value: str) -> None:
    sample = copy.deepcopy(EXAMPLE)
    sample["revue_core_min_version"] = bad_value
    with pytest.raises(ManifestError) as exc_info:
        validate(sample)
    assert "revue_core_min_version" in str(exc_info.value)


def test_revue_core_min_version_listed_in_schema_properties() -> None:
    """Smoke check that the schema we ship actually exposes the new field."""
    assert "revue_core_min_version" in MANIFEST_SCHEMA["properties"]
    field_schema = MANIFEST_SCHEMA["properties"]["revue_core_min_version"]
    assert field_schema["type"] == "string"
    assert "pattern" in field_schema

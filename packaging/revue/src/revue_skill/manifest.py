"""Release manifest schema validation.

The manifest served at revue.sh/skills/manifest.json lists the current release
version. The install script fetches the manifest and validates it against
``MANIFEST_SCHEMA`` before proceeding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def load_schema() -> dict[str, Any]:
    """Return the bundled JSON Schema document for the release manifest.

    Tries importlib.resources first (works in compiled wheels and installed
    packages), then falls back to the source-tree path for dev/test usage.
    """
    # Try importlib.resources (works in compiled wheels)
    try:
        from importlib.resources import files

        schema_text = files("revue_skill").joinpath("manifest.schema.json").read_text(encoding="utf-8")
        return json.loads(schema_text)
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass

    # Fallback for source-tree dev/test: schema is at packaging/revue/
    fallback_path = Path(__file__).resolve().parent.parent.parent / "manifest.schema.json"
    if fallback_path.is_file():
        return json.loads(fallback_path.read_text(encoding="utf-8"))

    raise FileNotFoundError(
        "manifest.schema.json not found. The schema must be bundled in the wheel "
        "or present in the source tree at packaging/revue/manifest.schema.json."
    )


MANIFEST_SCHEMA: dict[str, Any] = load_schema()
_VALIDATOR = Draft202012Validator(MANIFEST_SCHEMA)


@dataclass(frozen=True)
class ManifestError(Exception):
    """Raised when a fetched manifest fails schema validation."""

    message: str
    errors: tuple[str, ...]

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        joined = "; ".join(self.errors) if self.errors else self.message
        return f"{self.message}: {joined}"


def validate(manifest: dict[str, Any]) -> None:
    """Validate ``manifest`` against ``MANIFEST_SCHEMA``.

    :raises ManifestError: when one or more schema errors are present. All
        errors are collected so the caller gets a single message describing
        every problem.
    """
    errors = sorted(_VALIDATOR.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    raise ManifestError(
        message="manifest failed schema validation",
        errors=tuple(_format_error(err) for err in errors),
    )


def _format_error(err: ValidationError) -> str:
    location = "/".join(str(p) for p in err.absolute_path) or "(root)"
    return f"{location}: {err.message}"

"""Runtime dependency contract tests for the Fly web image."""
from __future__ import annotations

from pathlib import Path


def test_web_runtime_requirements_include_jsonschema_for_manifest_builder_startup():
    """The clean Fly image installs jsonschema before importing the web app."""
    # Arrange
    requirements_path = Path(__file__).resolve().parent.parent / "requirements.txt"

    # Act
    requirement_names = {
        line.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    # Assert
    assert "jsonschema" in requirement_names

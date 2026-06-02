"""Integration tests for skills routes."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock


def _override_builder(manifest=None, error=None):
    """Build a mock ManifestBuilder and register it as a dependency override.

    Returns a teardown callable that clears the override.
    """
    from main import app
    from routes.skills_routes import get_manifest_builder

    mock_builder = MagicMock()
    if error is not None:
        mock_builder.build_manifest = AsyncMock(side_effect=error)
    else:
        mock_builder.build_manifest = AsyncMock(return_value=manifest)

    app.dependency_overrides[get_manifest_builder] = lambda: mock_builder
    return lambda: app.dependency_overrides.pop(get_manifest_builder, None)


@pytest.fixture
def good_manifest() -> dict:
    return {
        "schema_version": 1,
        "current_version": "0.24.2",
        "released_at": "2026-05-28T10:33:05.392418Z",
        "artefacts": {
            "wheel": {
                "url": "https://files.pythonhosted.org/packages/revue-0.24.2-cp312-cp312-macosx_14_0_arm64.whl",
                "sha256": "b35e020014a530b07aade07414d0e1bbd6a28513c85deddfba083fd0b28a99ac",
                "size_bytes": 2188648,
            }
        },
    }


@pytest.mark.asyncio
async def test_get_skills_manifest_returns_200_with_valid_manifest(
    client: AsyncClient, good_manifest: dict
):
    """GET /skills/manifest.json returns 200 with schema-valid manifest."""
    teardown = _override_builder(manifest=good_manifest)
    try:
        resp = await client.get("/skills/manifest.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["current_version"] == "0.24.2"
        assert data["schema_version"] == 1
        assert data["released_at"] == "2026-05-28T10:33:05.392418Z"
    finally:
        teardown()


@pytest.mark.asyncio
async def test_get_skills_manifest_returns_500_on_manifest_builder_error(
    client: AsyncClient,
):
    """GET /skills/manifest.json returns 500 when manifest builder fails."""
    from services.manifest_builder import ManifestBuilderError

    teardown = _override_builder(error=ManifestBuilderError("PyPI error and no cache"))
    try:
        resp = await client.get("/skills/manifest.json")
        assert resp.status_code == 500
    finally:
        teardown()


@pytest.mark.asyncio
async def test_get_skills_manifest_response_validates_against_schema(
    client: AsyncClient, good_manifest: dict
):
    """GET /skills/manifest.json response validates against the bundled schema."""
    from services.manifest_builder import _VALIDATOR

    teardown = _override_builder(manifest=good_manifest)
    try:
        resp = await client.get("/skills/manifest.json")

        assert resp.status_code == 200
        data = resp.json()

        # Validate the served body against the real JSON Schema, not hand-rolled
        # key checks. _VALIDATOR is loaded from the bundled manifest.schema.json.
        assert _VALIDATOR is not None, "bundled manifest schema must be loadable"
        errors = list(_VALIDATOR.iter_errors(data))
        assert errors == [], f"schema violations: {[e.message for e in errors]}"
    finally:
        teardown()

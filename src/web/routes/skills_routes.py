"""Skills routes — serves manifests and skill metadata."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from httpx import AsyncClient

from services.manifest_builder import ManifestBuilder, ManifestBuilderError
from infra.pypi_client import PyPIClient

router = APIRouter()

logger = logging.getLogger(__name__)


def make_manifest_builder(http_client: AsyncClient, cache: dict[str, Any]) -> ManifestBuilder:
    """Construct a wired ManifestBuilder. Called once by the app composition root."""
    pypi_client = PyPIClient(http_client=http_client, logger=logger)
    return ManifestBuilder(pypi_client=pypi_client, cache=cache, logger=logger)


def get_manifest_builder(request: Request) -> ManifestBuilder:
    """FastAPI dependency: resolve the ManifestBuilder from app state.

    Overridable in tests via ``app.dependency_overrides``.
    """
    builder: ManifestBuilder | None = getattr(
        request.app.state, "manifest_builder", None
    )
    if builder is None:
        raise HTTPException(status_code=500, detail="Manifest builder not initialized")
    return builder


@router.get("/skills/manifest.json")
async def get_skills_manifest(
    builder: ManifestBuilder = Depends(get_manifest_builder),
) -> dict[str, Any]:
    """Return version manifest for the revue package.

    The manifest includes current version and published wheel metadata from PyPI.
    Cached with short TTL to minimize PyPI requests.

    Returns:
        Manifest JSON validating against the bundled manifest.schema.json

    Raises:
        HTTPException: 500 if manifest cannot be built (PyPI error + no cache)
    """
    try:
        return await builder.build_manifest(package_name="revue", ttl_seconds=300)
    except ManifestBuilderError as e:
        logger.error(f"Failed to build manifest: {e}")
        raise HTTPException(status_code=500, detail="Cannot build manifest")

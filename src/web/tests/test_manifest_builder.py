"""Unit tests for manifest builder service."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Note: will fail until services module is created
pytest.importorskip("services.manifest_builder")


@pytest.fixture
def pypi_release_fixture() -> dict:
    """Captured PyPI JSON for revue-0.24.2.

    Mirrors the real response: per-platform cp312 wheel, no-timezone
    `upload_time`, and the `upload_time_iso_8601` variant the builder consumes.
    """
    return {
        "info": {
            "version": "0.24.2",
            "release_url": "https://pypi.org/project/revue/0.24.2/",
            "upload_time": "2026-05-28T10:33:05",
        },
        "releases": {
            "0.24.2": [
                {
                    "filename": "revue-0.24.2-cp312-cp312-macosx_14_0_arm64.whl",
                    "url": "https://files.pythonhosted.org/packages/revue-0.24.2-cp312-cp312-macosx_14_0_arm64.whl",
                    "digests": {
                        "sha256": "b35e020014a530b07aade07414d0e1bbd6a28513c85deddfba083fd0b28a99ac"
                    },
                    "size": 2188648,
                    "upload_time": "2026-05-28T10:33:05",
                    "upload_time_iso_8601": "2026-05-28T10:33:05.392418Z",
                }
            ]
        },
    }


@pytest.mark.asyncio
async def test_build_manifest_maps_pypi_json_to_schema_valid_manifest(
    pypi_release_fixture,
):
    """Manifest builder transforms PyPI JSON → schema-valid manifest."""
    from services.manifest_builder import ManifestBuilder

    # Arrange
    pypi_client = AsyncMock()
    pypi_client.fetch_release_info.return_value = pypi_release_fixture
    cache = {}
    logger = MagicMock()
    builder = ManifestBuilder(pypi_client=pypi_client, cache=cache, logger=logger)

    # Act
    manifest = await builder.build_manifest(package_name="revue", ttl_seconds=300)

    # Assert — structure and content correct
    assert manifest["schema_version"] == 1
    assert manifest["current_version"] == "0.24.2"
    # released_at must be the timezone-aware ISO-8601 variant (RFC 3339).
    assert manifest["released_at"] == "2026-05-28T10:33:05.392418Z"
    assert (
        manifest["artefacts"]["wheel"]["sha256"]
        == "b35e020014a530b07aade07414d0e1bbd6a28513c85deddfba083fd0b28a99ac"
    )
    assert manifest["artefacts"]["wheel"]["size_bytes"] == 2188648
    assert (
        manifest["artefacts"]["wheel"]["url"]
        == "https://files.pythonhosted.org/packages/revue-0.24.2-cp312-cp312-macosx_14_0_arm64.whl"
    )


@pytest.mark.asyncio
async def test_build_manifest_caches_result(pypi_release_fixture):
    """Manifest builder caches result and does not re-fetch on subsequent calls."""
    from services.manifest_builder import ManifestBuilder

    # Arrange
    pypi_client = AsyncMock()
    pypi_client.fetch_release_info.return_value = pypi_release_fixture
    cache = {}
    logger = MagicMock()
    builder = ManifestBuilder(pypi_client=pypi_client, cache=cache, logger=logger)

    # Act
    manifest1 = await builder.build_manifest(package_name="revue", ttl_seconds=300)
    manifest2 = await builder.build_manifest(package_name="revue", ttl_seconds=300)

    # Assert — same manifest returned, PyPI called only once
    assert manifest1 == manifest2
    assert pypi_client.fetch_release_info.call_count == 1


@pytest.mark.asyncio
async def test_build_manifest_falls_back_to_cache_on_pypi_error(
    pypi_release_fixture,
):
    """On PyPI error, manifest builder returns cached value if available."""
    from infra.pypi_client import PyPIClientError
    from services.manifest_builder import ManifestBuilder

    # Arrange
    pypi_client = AsyncMock()
    cache = {}
    logger = MagicMock()
    builder = ManifestBuilder(pypi_client=pypi_client, cache=cache, logger=logger)

    # First call succeeds and caches
    pypi_client.fetch_release_info.return_value = pypi_release_fixture
    manifest_good = await builder.build_manifest(package_name="revue", ttl_seconds=300)

    # Force the cache entry to be stale so the second call actually attempts a
    # fresh fetch and exercises the PyPIClientError fallback branch (rather than
    # short-circuiting on a still-fresh cache hit).
    cached_time, cached_manifest = cache["revue"]
    cache["revue"] = (cached_time - 10_000, cached_manifest)

    # Second call: fetch fails; builder should fall back to the stale cache.
    pypi_client.fetch_release_info.side_effect = PyPIClientError("PyPI down")
    manifest_fallback = await builder.build_manifest(
        package_name="revue", ttl_seconds=300
    )

    # Assert — fallback returns cached manifest and a fresh fetch was attempted
    assert manifest_fallback == manifest_good
    assert pypi_client.fetch_release_info.call_count == 2


@pytest.mark.asyncio
async def test_build_manifest_raises_on_pypi_error_with_no_cache():
    """On PyPI error with no cache, manifest builder raises exception."""
    from infra.pypi_client import PyPIClientError
    from services.manifest_builder import ManifestBuilder, ManifestBuilderError

    # Arrange
    pypi_client = AsyncMock()
    pypi_client.fetch_release_info.side_effect = PyPIClientError("PyPI down")
    cache = {}
    logger = MagicMock()
    builder = ManifestBuilder(pypi_client=pypi_client, cache=cache, logger=logger)

    # Act & Assert
    with pytest.raises(ManifestBuilderError):
        await builder.build_manifest(package_name="revue", ttl_seconds=300)


@pytest.mark.asyncio
async def test_build_manifest_raises_on_schema_invalid_output(pypi_release_fixture):
    """A built manifest that violates the schema raises ManifestBuilderError (→500).

    Exercises the rejection branch of _validate, not just the happy path.
    """
    from services.manifest_builder import (
        ManifestBuilder,
        ManifestBuilderError,
        _VALIDATOR,
    )

    if _VALIDATOR is None:
        pytest.skip("schema not loadable in this environment")

    bad = json.loads(json.dumps(pypi_release_fixture))
    # sha256 that violates the schema pattern ^[0-9a-f]{64}$
    bad["releases"]["0.24.2"][0]["digests"]["sha256"] = "NOT-A-VALID-SHA256"
    pypi_client = AsyncMock()
    pypi_client.fetch_release_info.return_value = bad
    builder = ManifestBuilder(pypi_client=pypi_client, cache={}, logger=MagicMock())

    with pytest.raises(ManifestBuilderError):
        await builder.build_manifest(package_name="revue", ttl_seconds=300)


@pytest.mark.asyncio
async def test_build_manifest_validation_failure_does_not_serve_stale_cache(
    pypi_release_fixture,
):
    """A schema-invalid fresh build raises even when a valid cache exists.

    Validation failure is a code/schema bug, not a transient outage, so it must
    surface as 5xx rather than be masked by a stale cached manifest.
    """
    from services.manifest_builder import (
        ManifestBuilder,
        ManifestValidationError,
        _VALIDATOR,
    )

    if _VALIDATOR is None:
        pytest.skip("schema not loadable in this environment")

    cache: dict = {}
    pypi_client = AsyncMock()
    pypi_client.fetch_release_info.return_value = pypi_release_fixture
    builder = ManifestBuilder(pypi_client=pypi_client, cache=cache, logger=MagicMock())

    # Populate cache with a good manifest, then expire it.
    await builder.build_manifest(package_name="revue", ttl_seconds=300)
    cached_time, cached_manifest = cache["revue"]
    cache["revue"] = (cached_time - 10_000, cached_manifest)

    # Next fetch returns a schema-invalid payload (bad sha256).
    bad = json.loads(json.dumps(pypi_release_fixture))
    bad["releases"]["0.24.2"][0]["digests"]["sha256"] = "NOT-A-VALID-SHA256"
    pypi_client.fetch_release_info.return_value = bad

    with pytest.raises(ManifestValidationError):
        await builder.build_manifest(package_name="revue", ttl_seconds=300)


def test_vendored_schema_matches_canonical():
    """The vendored web schema must stay byte-identical to the canonical source.

    The web Docker image only ships src/web/manifest.schema.json, but CI runs
    against the full source tree — so drift fails the build here rather than
    silently serving a stale schema in production.
    """
    web_schema = Path(__file__).resolve().parent.parent / "manifest.schema.json"
    canonical = (
        Path(__file__).resolve().parents[3]
        / "packaging"
        / "revue"
        / "manifest.schema.json"
    )
    if not canonical.is_file():
        pytest.skip("canonical schema not present (not a full source checkout)")
    assert web_schema.read_bytes() == canonical.read_bytes(), (
        "src/web/manifest.schema.json has drifted from "
        "packaging/revue/manifest.schema.json — re-copy the canonical schema."
    )

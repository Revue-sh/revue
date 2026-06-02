"""Manifest builder service — orchestrates PyPI fetch, caching, and fallback logic."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from infra.pypi_client import PyPIClient, PyPIClientError

# Vendored copy of packaging/revue/manifest.schema.json. The web app's Docker
# build context is src/web/ only, so the canonical schema under packaging/ is
# not present at runtime — mirror it here, the same way the revue_skill wheel
# bundles its own copy. Keep this file in sync with the canonical schema.
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "manifest.schema.json"


def _load_validator() -> Draft202012Validator | None:
    """Load the manifest JSON Schema validator, best-effort.

    Returns None (and validation is skipped) if the vendored schema file is
    missing, so a packaging slip degrades to "no validation" rather than
    crashing the whole web app at import time.
    """
    try:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        return Draft202012Validator(schema)
    except (OSError, ValueError):
        return None


_VALIDATOR = _load_validator()


class ManifestBuilderError(Exception):
    """Raised when manifest builder cannot produce a valid manifest."""

    pass


class ManifestValidationError(ManifestBuilderError):
    """Raised when a built manifest fails schema validation.

    Distinct from the base error so the builder can treat it differently: a
    schema-invalid build is a code/schema bug and must surface as a 5xx, never
    be masked by serving a stale cached manifest.
    """

    pass


class ManifestBuilder:
    """Builds version manifest from PyPI metadata.

    Responsibilities:
    - Fetch package metadata from PyPI (via injected client)
    - Map PyPI JSON to manifest schema
    - Cache results with TTL
    - Fallback to cached manifest on PyPI errors

    Constructor injection pattern.
    """

    def __init__(
        self,
        pypi_client: PyPIClient,
        cache: dict[str, Any],
        logger: logging.Logger,
    ):
        """Initialize manifest builder.

        Args:
            pypi_client: PyPI client for fetching metadata
            cache: Shared cache dict (package_name → (timestamp, manifest))
            logger: Logger instance for info/warning/error messages
        """
        self.pypi_client = pypi_client
        self.cache = cache
        self.logger = logger

    async def build_manifest(
        self, package_name: str, ttl_seconds: int = 300
    ) -> dict[str, Any]:
        """Build version manifest for a package.

        Args:
            package_name: Package name (e.g., "revue")
            ttl_seconds: Cache TTL in seconds (default 5 min)

        Returns:
            Schema-valid manifest dict with current_version, artefacts, schema_version

        Raises:
            ManifestBuilderError: If PyPI fails and no cached value available
        """
        cache_key = package_name

        # Check cache validity
        if cache_key in self.cache:
            cached_time, cached_manifest = self.cache[cache_key]
            if time.time() - cached_time < ttl_seconds:
                self.logger.info(f"Serving cached manifest for {package_name}")
                return cached_manifest

        # Fetch + map. A failure here is either a transport error
        # (PyPIClientError) or a PyPI shape change (ManifestBuilderError from
        # _map_pypi_to_manifest) — both external and transient-ish, so prefer a
        # stale-but-valid cached manifest over a 500.
        try:
            pypi_data = await self.pypi_client.fetch_release_info(package_name)
            manifest = self._map_pypi_to_manifest(pypi_data)
        except (PyPIClientError, ManifestBuilderError) as e:
            self.logger.error(f"Manifest fetch/map failed for {package_name}: {e}")

            # Fallback to cached value if available (even if expired)
            if cache_key in self.cache:
                _, cached_manifest = self.cache[cache_key]
                self.logger.warning(
                    f"PyPI unavailable, serving stale manifest for {package_name}"
                )
                return cached_manifest

            # No cache and fetch failed
            raise ManifestBuilderError(
                f"Cannot build manifest for {package_name}: {e}"
            ) from e

        # Validation is deliberately OUTSIDE the fallback: a schema-invalid build
        # is a code/schema bug, not a transient outage. Surface it as a 5xx
        # (ManifestValidationError propagates) rather than masking it by serving
        # a stale cached manifest.
        self._validate(manifest)
        self.cache[cache_key] = (time.time(), manifest)
        return manifest

    def _validate(self, manifest: dict[str, Any]) -> None:
        """Validate a built manifest against the bundled JSON Schema.

        Raises ManifestValidationError on a schema violation (a builder bug, not
        a transient error). Skipped silently if the schema file was not loadable.
        """
        if _VALIDATOR is None:
            self.logger.warning("Manifest schema unavailable; skipping validation")
            return
        errors = sorted(
            _VALIDATOR.iter_errors(manifest), key=lambda e: list(e.absolute_path)
        )
        if errors:
            detail = "; ".join(e.message for e in errors)
            raise ManifestValidationError(
                f"Built manifest failed schema validation: {detail}"
            )

    def _map_pypi_to_manifest(self, pypi_data: dict[str, Any]) -> dict[str, Any]:
        """Map PyPI JSON to manifest schema.

        Args:
            pypi_data: Raw PyPI JSON response

        Returns:
            Manifest dict matching the schema

        Raises:
            ManifestBuilderError: If required fields missing or structure invalid
        """
        try:
            info = pypi_data["info"]
            version = info["version"]
            releases = pypi_data["releases"]

            # Find the wheel for this version
            version_releases = releases.get(version, [])
            wheel = None
            for release in version_releases:
                if release["filename"].endswith(".whl"):
                    wheel = release
                    break

            if not wheel:
                raise ManifestBuilderError(f"No wheel found for version {version}")

            # Use the ISO-8601 variant: PyPI's bare `upload_time` has no timezone
            # designator (e.g. "2026-05-28T10:33:05"), which fails the schema's
            # RFC 3339 `date-time` format. `upload_time_iso_8601` carries the `Z`.
            released_at = wheel.get("upload_time_iso_8601") or wheel["upload_time"]

            manifest = {
                "schema_version": 1,
                "current_version": version,
                "released_at": released_at,
                "artefacts": {
                    "wheel": {
                        "url": wheel["url"],
                        "sha256": wheel["digests"]["sha256"],
                        "size_bytes": wheel["size"],
                    }
                },
            }

            return manifest
        except (KeyError, IndexError, TypeError) as e:
            raise ManifestBuilderError(f"Invalid PyPI data structure: {e}") from e

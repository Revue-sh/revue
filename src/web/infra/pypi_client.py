"""PyPI client infra layer — fetches package metadata from PyPI."""
from __future__ import annotations

import json
import logging
from typing import Any

from httpx import AsyncClient, HTTPError


class PyPIClientError(Exception):
    """Raised when PyPI client fails to fetch or parse package metadata."""

    pass


class PyPIClient:
    """Fetches package metadata from PyPI.org API.

    Constructor injection pattern: receives HTTP client and logger as dependencies.
    """

    PYPI_BASE_URL = "https://pypi.org/pypi"

    def __init__(self, http_client: AsyncClient, logger: logging.Logger):
        """Initialize PyPI client.

        Args:
            http_client: AsyncClient for making HTTP requests
            logger: Logger instance for info/warning messages
        """
        self.http_client = http_client
        self.logger = logger

    async def fetch_release_info(self, package_name: str) -> dict[str, Any]:
        """Fetch package metadata from PyPI.

        Args:
            package_name: Package name (e.g., "revue")

        Returns:
            Parsed PyPI JSON response containing info and releases data

        Raises:
            PyPIClientError: If fetch fails or JSON parsing fails
        """
        url = f"{self.PYPI_BASE_URL}/{package_name}/json"

        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            # httpx Response.json() is synchronous — do NOT await it.
            data = response.json()
            return data
        except HTTPError as e:
            # httpx.HTTPError is the base of both transport errors (connect,
            # timeout) and HTTPStatusError, so this covers every network/4xx/5xx
            # failure mode.
            self.logger.error(f"HTTP error fetching PyPI metadata for {package_name}: {e}")
            raise PyPIClientError(f"Failed to fetch PyPI metadata: {e}") from e
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parse error for PyPI response: {e}")
            raise PyPIClientError(f"Failed to parse PyPI response: {e}") from e

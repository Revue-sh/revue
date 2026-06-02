"""Unit tests for PyPI client infra layer."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Note: will fail until infra module is created
pytest.importorskip("infra.pypi_client")


@pytest.fixture
def pypi_release_fixture() -> dict:
    """Captured PyPI JSON fixture for revue-0.24.2.

    Mirrors the real PyPI response shape: a per-platform Nuitka wheel
    (cp312, not py3-none-any), a no-timezone `upload_time`, and the
    `upload_time_iso_8601` variant the builder actually consumes.
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
async def test_fetch_release_info_returns_parsed_pypi_json():
    """PyPI client fetches and parses PyPI JSON for given package."""
    from infra.pypi_client import PyPIClient

    # Arrange
    http_client = AsyncMock()
    logger = MagicMock()
    pypi_fixture = {
        "info": {"version": "0.24.2"},
        "releases": {"0.24.2": []},
    }
    # httpx Response.json() is synchronous — mock it as a sync method so this
    # test fails if the client ever `await`s it again.
    http_client.get.return_value = MagicMock(json=MagicMock(return_value=pypi_fixture))
    client = PyPIClient(http_client=http_client, logger=logger)

    # Act
    result = await client.fetch_release_info("revue")

    # Assert — returns parsed JSON
    assert result == pypi_fixture
    http_client.get.assert_called_once_with("https://pypi.org/pypi/revue/json")


@pytest.mark.asyncio
async def test_fetch_release_info_raises_on_http_error():
    """PyPI client raises on HTTP error (4xx/5xx)."""
    from infra.pypi_client import PyPIClient, PyPIClientError

    # Arrange
    http_client = AsyncMock()
    logger = MagicMock()
    response = MagicMock()
    response.status_code = 404
    # Use a real httpx.HTTPStatusError so the `except HTTPError` branch is
    # actually exercised (a bare Exception would slip past it).
    request = httpx.Request("GET", "https://pypi.org/pypi/nonexistent-package/json")
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404 Not Found", request=request, response=httpx.Response(404, request=request)
    )
    http_client.get.return_value = response
    client = PyPIClient(http_client=http_client, logger=logger)

    # Act & Assert
    with pytest.raises(PyPIClientError):
        await client.fetch_release_info("nonexistent-package")


@pytest.mark.asyncio
async def test_fetch_release_info_raises_on_json_parse_error():
    """PyPI client raises on invalid/unparseable JSON."""
    from infra.pypi_client import PyPIClient, PyPIClientError

    # Arrange
    http_client = AsyncMock()
    logger = MagicMock()
    http_client.get.return_value = MagicMock(
        json=MagicMock(side_effect=json.JSONDecodeError("msg", "doc", 0))
    )
    client = PyPIClient(http_client=http_client, logger=logger)

    # Act & Assert
    with pytest.raises(PyPIClientError):
        await client.fetch_release_info("revue")


@pytest.mark.asyncio
async def test_fetch_release_info_with_realistic_fixture(pypi_release_fixture):
    """PyPI client correctly parses realistic PyPI JSON structure."""
    from infra.pypi_client import PyPIClient

    # Arrange
    http_client = AsyncMock()
    logger = MagicMock()
    http_client.get.return_value = MagicMock(
        json=MagicMock(return_value=pypi_release_fixture)
    )
    client = PyPIClient(http_client=http_client, logger=logger)

    # Act
    result = await client.fetch_release_info("revue")

    # Assert — payload matches fixture exactly
    assert result["info"]["version"] == "0.24.2"
    assert (
        result["releases"]["0.24.2"][0]["filename"]
        == "revue-0.24.2-cp312-cp312-macosx_14_0_arm64.whl"
    )
    assert (
        result["releases"]["0.24.2"][0]["digests"]["sha256"]
        == "b35e020014a530b07aade07414d0e1bbd6a28513c85deddfba083fd0b28a99ac"
    )
    assert result["releases"]["0.24.2"][0]["size"] == 2188648

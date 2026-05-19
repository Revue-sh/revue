"""Tests for ``cli._fetch_manifest`` URL validation (REVUE-275 medium finding).

The ``--manifest-url`` flag accepts a user-supplied URL that is then passed to
``urllib.request.urlopen``. Without scheme validation, a wrapper script or
malicious local process could point at ``file://`` or ``http://`` to bypass
HTTPS protections, or at an internal service. The ``# noqa: S310`` suppression
on the urlopen call assumes a fixed-domain HTTPS call.
"""

from __future__ import annotations

import pytest

from revue_skill.cli import ManifestURLError, _validate_manifest_url


def test_https_url_accepted() -> None:
    _validate_manifest_url("https://revue.sh/skills/manifest.json")


def test_http_url_rejected() -> None:
    with pytest.raises(ManifestURLError) as exc_info:
        _validate_manifest_url("http://revue.sh/skills/manifest.json")
    assert "https" in str(exc_info.value).lower()


def test_file_url_rejected() -> None:
    with pytest.raises(ManifestURLError):
        _validate_manifest_url("file:///etc/passwd")


def test_ftp_url_rejected() -> None:
    with pytest.raises(ManifestURLError):
        _validate_manifest_url("ftp://revue.sh/manifest.json")


def test_missing_scheme_rejected() -> None:
    with pytest.raises(ManifestURLError):
        _validate_manifest_url("revue.sh/manifest.json")


def test_missing_host_rejected() -> None:
    with pytest.raises(ManifestURLError):
        _validate_manifest_url("https:///manifest.json")


@pytest.mark.parametrize("bad_url", ["", "javascript:alert(1)", "data:text/json,{}"])
def test_obviously_bad_url_rejected(bad_url: str) -> None:
    with pytest.raises(ManifestURLError):
        _validate_manifest_url(bad_url)

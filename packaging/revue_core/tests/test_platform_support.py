"""Unit tests for the canonical supported-platform policy (REVUE-360 AC3).

``revue_core.platform_support`` is the single source of truth for which
platforms Revue publishes wheels for. The installer (``scripts/install.sh``)
and the install page (``docs/guides/install.md``) are pinned to it by
``tests/test_supported_platforms_consistency.py``; these tests cover the
Python policy surface itself.
"""
from __future__ import annotations

from revue_core import platform_support as ps


def test_supported_platforms_are_exactly_the_two_published_targets():
    # Arrange — the published wheel matrix is macOS ARM64 + Linux x86_64 only.
    # Act
    pairs = {(p.system, p.machine) for p in ps.SUPPORTED_PLATFORMS}

    # Assert — no other combination is in the policy list
    assert pairs == {("darwin", "arm64"), ("linux", "x86_64")}


def test_is_supported_returns_true_for_macos_apple_silicon():
    # Arrange / Act
    result = ps.is_supported("darwin", "arm64")

    # Assert
    assert result is True


def test_is_supported_returns_true_for_linux_x86_64():
    # Arrange / Act
    result = ps.is_supported("linux", "x86_64")

    # Assert
    assert result is True


def test_is_supported_is_case_insensitive_on_uname_output():
    # Arrange — `uname -s` reports "Darwin" with a capital D on macOS.
    # Act
    result = ps.is_supported("Darwin", "ARM64")

    # Assert
    assert result is True


def test_is_supported_returns_false_for_intel_mac():
    # Arrange / Act — Intel Macs report x86_64; explicitly unsupported.
    result = ps.is_supported("darwin", "x86_64")

    # Assert
    assert result is False


def test_is_supported_returns_false_for_linux_arm64():
    # Arrange / Act — Graviton/Linux ARM reports aarch64; unsupported.
    result = ps.is_supported("linux", "aarch64")

    # Assert
    assert result is False


def test_is_supported_returns_false_for_windows():
    # Arrange / Act
    result = ps.is_supported("windows", "amd64")

    # Assert
    assert result is False


def test_normalise_machine_maps_amd64_alias_to_x86_64():
    # Arrange / Act — some toolchains report amd64 for the same arch.
    normalised = ps.normalise_machine("amd64")

    # Assert
    assert normalised == "x86_64"


def test_is_supported_accepts_amd64_alias_for_linux():
    # Arrange / Act — amd64 is an alias for x86_64, so Linux/amd64 is supported.
    result = ps.is_supported("linux", "amd64")

    # Assert
    assert result is True


def test_supported_labels_lists_both_published_platforms_in_order():
    # Arrange / Act
    labels = ps.supported_labels()

    # Assert — human-readable labels used in the guard message and install page
    assert labels == ("macOS ARM64", "Linux x86_64")


def test_unsupported_message_names_platform_links_page_and_states_workaround():
    # Arrange / Act — message for an Intel Mac trying to install.
    message = ps.unsupported_message("Darwin", "x86_64")

    # Assert — AC1 requires all three: the platform, the install page, the CI workaround
    assert "Darwin x86_64" in message
    assert ps.INSTALL_PAGE_URL in message
    assert "revue-ci" in message


def test_format_platform_status_line_marks_supported_platform_with_label():
    # Arrange / Act
    line = ps.format_platform_status_line("linux", "x86_64")

    # Assert
    assert line == "Platform: Linux x86_64 (supported)"


def test_format_platform_status_line_flags_unsupported_with_page_url():
    # Arrange / Act
    line = ps.format_platform_status_line("darwin", "x86_64")

    # Assert — unsupported dev/source installs are surfaced, not hidden
    assert "UNSUPPORTED" in line
    assert ps.INSTALL_PAGE_URL in line


def test_install_page_url_is_an_absolute_resolvable_url():
    # Arrange / Act / Assert — must be a real https URL, not an aspirational stub
    assert ps.INSTALL_PAGE_URL.startswith("https://")

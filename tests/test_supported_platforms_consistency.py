"""REVUE-360 AC3: the supported-platform list must be single-sourced.

``revue_core.platform_support`` is the source of truth. The installer
(``scripts/install.sh``) and the install page (``docs/guides/install.md``)
cannot import Python at the point they need it, so they necessarily restate
the list — these tests fail CI the moment any restatement drifts from the
canonical Python definition. That is what stops "three copy-pasted lists that
can drift" (AC3).
"""
from __future__ import annotations

import re
from pathlib import Path

from revue_core import platform_support as ps

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_MD = REPO_ROOT / "docs" / "guides" / "install.md"


def _shell_supported_pairs() -> set[tuple[str, str]]:
    """Parse the ``SUPPORTED_PLATFORMS=( ... )`` array out of install.sh.

    Robust to reformatting: bash line comments are stripped first (an inline
    comment may contain ``")"``), the entries are matched with flexible
    whitespace, and the parsed count is asserted against the canonical list so
    a silently-dropped line fails the test instead of slipping through set
    equality.
    """
    raw = INSTALL_SH.read_text()
    text = re.sub(r"#[^\n]*", "", raw)  # drop bash line comments
    block = re.search(r"SUPPORTED_PLATFORMS=\((.*?)\)", text, re.S)
    assert block, "install.sh must declare a SUPPORTED_PLATFORMS array"
    tokens = re.findall(r'"([a-z0-9_]+)\s+([a-z0-9_]+)"', block.group(1))
    assert len(tokens) == len(ps.SUPPORTED_PLATFORMS), (
        f"install.sh SUPPORTED_PLATFORMS parsed {len(tokens)} entries, expected "
        f"{len(ps.SUPPORTED_PLATFORMS)} — a reformat may have hidden a platform"
    )
    return set(tokens)


def _shell_install_page_url() -> str:
    text = INSTALL_SH.read_text()
    match = re.search(r'INSTALL_PAGE_URL="([^"]+)"', text)
    assert match, "install.sh must declare INSTALL_PAGE_URL"
    return match.group(1)


def test_installer_supported_list_matches_revue_core():
    # Arrange
    canonical = {(p.system, p.machine) for p in ps.SUPPORTED_PLATFORMS}

    # Act
    shell = _shell_supported_pairs()

    # Assert — the shell guard and the Python policy agree exactly
    assert shell == canonical, (
        "scripts/install.sh SUPPORTED_PLATFORMS has drifted from "
        "revue_core.platform_support.SUPPORTED_PLATFORMS"
    )


def test_installer_install_page_url_matches_revue_core():
    # Arrange / Act / Assert
    assert _shell_install_page_url() == ps.INSTALL_PAGE_URL


def test_installer_guard_message_labels_match_revue_core():
    # Arrange — the guard's printed "Supported platforms: ..." line is a
    # hand-written bash copy; pin every label to revue_core so it cannot drift.
    text = INSTALL_SH.read_text()

    # Act / Assert
    for label in ps.supported_labels():
        assert label in text, (
            f"install.sh guard message must name supported platform {label!r} "
            "(drifted from revue_core.platform_support.supported_labels)"
        )


def test_installer_guard_message_states_canonical_ci_workaround():
    # Arrange / Act — the workaround wording is single-sourced in revue_core.
    text = INSTALL_SH.read_text()

    # Assert — the exact CI_WORKAROUND string appears in the guard message
    assert ps.CI_WORKAROUND in text, (
        "install.sh guard message must embed revue_core.platform_support."
        "CI_WORKAROUND verbatim so the workaround text cannot drift"
    )


def test_install_page_lists_every_supported_platform_label():
    # Arrange
    md = INSTALL_MD.read_text()

    # Act / Assert — each canonical label is documented on the install page
    for label in ps.supported_labels():
        assert label in md, f"install.md must list supported platform: {label}"


def test_install_page_links_the_canonical_install_url():
    # Arrange / Act / Assert
    md = INSTALL_MD.read_text()
    assert ps.INSTALL_PAGE_URL in md


def test_install_page_documents_raw_pip_limitation_and_redirect():
    # Arrange — AC4 / TC3: raw `pip install revue` on an unsupported platform is
    # a documented best-effort limitation; the docs must name the generic pip
    # error and redirect to the supported install path + CI workaround.
    md = INSTALL_MD.read_text()

    # Assert
    assert "no matching distribution" in md.lower()
    assert "revue-ci" in md, "install page must state the CI workaround"

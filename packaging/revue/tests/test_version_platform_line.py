"""REVUE-360 AC3: ``revue version`` surfaces the canonical supported-platform
status line, so the skill wheel shares one source of truth with revue_core.
"""
from __future__ import annotations

import platform

from revue_core.platform_support import format_platform_status_line
from revue_skill import __version__
from revue_skill import cli


def test_dashed_version_flag_succeeds_and_prints_version(capsys):
    # Arrange — `revue --version` is the installer's final verify step; the
    # outer parser uses required subcommands, so it must be pre-routed.
    # Act
    rc = cli.main(["--version"])

    # Assert — exits 0 (not argparse's exit-2) and prints the version
    out = capsys.readouterr().out
    assert rc == 0
    assert __version__ in out


def test_short_version_flag_is_equivalent_to_dashed(capsys):
    # Arrange / Act
    rc = cli.main(["-V"])

    # Assert
    out = capsys.readouterr().out
    assert rc == 0
    assert __version__ in out


def test_version_emits_canonical_platform_status_line(capsys):
    # Arrange — line derived from the single source of truth, not duplicated here.
    expected_line = format_platform_status_line(platform.system(), platform.machine())

    # Act
    rc = cli.main(["version"])

    # Assert — version still succeeds and now names the platform
    out = capsys.readouterr().out
    assert rc == 0
    assert expected_line in out


def test_version_warns_with_full_message_on_unsupported_platform(capsys, monkeypatch):
    # Arrange — simulate a source/editable install on an Intel Mac.
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    # Act
    rc = cli.main(["version"])

    # Assert — succeeds, but the full actionable guidance is surfaced on stderr
    captured = capsys.readouterr()
    assert rc == 0
    assert "UNSUPPORTED" in captured.out
    assert "Darwin x86_64" in captured.err
    assert "revue-ci" in captured.err

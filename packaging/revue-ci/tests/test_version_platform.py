"""REVUE-360 AC3: ``revue-ci version`` references the canonical supported-platform
list, so revue-ci shares one source of truth with revue_core / the installer.
"""
from __future__ import annotations

import platform

from revue_core.platform_support import format_platform_status_line
from revue_ci.cli import build_parser


def _parse_args(argv: list[str]):
    return build_parser().parse_args(argv)


def test_version_command_prints_canonical_platform_status_line(capsys):
    # Arrange — the line is derived from revue_core, not hand-written here.
    expected_line = format_platform_status_line(platform.system(), platform.machine())
    args = _parse_args(["version"])

    # Act
    rc = args.func(args)

    # Assert — version command succeeds and surfaces the shared platform line
    out = capsys.readouterr().out
    assert rc == 0
    assert expected_line in out


def test_version_command_prints_a_version_string(capsys):
    # Arrange
    args = _parse_args(["version"])

    # Act
    rc = args.func(args)

    # Assert — succeeds, and the first line is the "revue-ci <version>" token
    out = capsys.readouterr().out
    assert rc == 0
    first_line = out.splitlines()[0]
    assert first_line.startswith("revue-ci ")
    assert first_line.split("revue-ci ", 1)[1].strip(), "version token must be non-empty"


def test_version_command_falls_back_to_unknown_when_metadata_absent(capsys, monkeypatch):
    # Arrange — simulate a source-tree run with no installed dist metadata.
    import importlib.metadata as md

    def _raise(_name):
        raise md.PackageNotFoundError("revue-ci")

    monkeypatch.setattr(md, "version", _raise)
    args = _parse_args(["version"])

    # Act
    rc = args.func(args)

    # Assert — degrades gracefully rather than crashing on the missing metadata
    out = capsys.readouterr().out
    assert rc == 0
    assert "revue-ci unknown" in out


def test_version_command_warns_full_message_on_unsupported_platform(capsys, monkeypatch):
    # Arrange — simulate running revue-ci from source on Linux ARM (Graviton).
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    args = _parse_args(["version"])

    # Act
    rc = args.func(args)

    # Assert — exit 0, but the actionable unsupported message lands on stderr
    captured = capsys.readouterr()
    assert rc == 0
    assert "UNSUPPORTED" in captured.out
    assert "Linux aarch64" in captured.err
    assert "revue-ci" in captured.err

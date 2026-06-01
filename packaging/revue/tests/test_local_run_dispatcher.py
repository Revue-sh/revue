"""REVUE-369 F4+F6: tests for the local-run subcommand dispatcher.

These tests would have caught the original bug where dispatch_local_run
called lr_module.main(argv=...) but local_run.main() took no arguments.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PACKAGING_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def test_local_run_main_accepts_argv_kwarg():
    # Arrange — import the real local_run module from the source tree
    repo_root = PACKAGING_DIR.parent.parent
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    # Act — import and inspect signature
    import inspect

    import local_run

    sig = inspect.signature(local_run.main)

    # Assert — main() accepts the argv kwarg the dispatcher passes
    assert "argv" in sig.parameters, (
        "local_run.main() must accept an 'argv' kwarg so dispatch_local_run "
        "can forward CLI args without mutating sys.argv (REVUE-369 H1)"
    )


def test_dispatch_local_run_returns_zero_on_systemexit_none():
    # Arrange
    from revue_skill.skill.local_run_dispatcher import dispatch_local_run

    # Set up a fake local_run module that raises SystemExit() (clean exit)
    import types

    fake_mod = types.ModuleType("revue_skill.skill.local_run")

    def fake_main(argv=None):
        raise SystemExit()  # equivalent to sys.exit() — success

    fake_mod.main = fake_main
    sys.modules["revue_skill.skill.local_run"] = fake_mod

    try:
        # Act
        code = dispatch_local_run("position", ["--all"])

        # Assert — SystemExit() means success, dispatcher must return 0
        assert code == 0, "SystemExit() means clean exit and must map to return 0"
    finally:
        del sys.modules["revue_skill.skill.local_run"]


def test_dispatch_local_run_returns_exit_code_on_systemexit_int():
    # Arrange
    from revue_skill.skill.local_run_dispatcher import dispatch_local_run

    import types

    fake_mod = types.ModuleType("revue_skill.skill.local_run")

    def fake_main(argv=None):
        raise SystemExit(2)  # argparse parse error

    fake_mod.main = fake_main
    sys.modules["revue_skill.skill.local_run"] = fake_mod

    try:
        # Act
        code = dispatch_local_run("position", [])

        # Assert
        assert code == 2, "SystemExit(2) must propagate through dispatcher"
    finally:
        del sys.modules["revue_skill.skill.local_run"]


def test_dispatch_local_run_forwards_argv_to_main():
    # Arrange — capture argv as it's passed into fake main
    from revue_skill.skill.local_run_dispatcher import dispatch_local_run

    import types

    fake_mod = types.ModuleType("revue_skill.skill.local_run")
    captured_argv = []

    def fake_main(argv=None):
        captured_argv.append(argv)
        return 0

    fake_mod.main = fake_main
    sys.modules["revue_skill.skill.local_run"] = fake_mod

    try:
        # Act
        dispatch_local_run("position", ["--all", "--platform", "github"])

        # Assert — dispatcher forwards subcommand + args as argv to local_run.main
        assert captured_argv == [["position", "--all", "--platform", "github"]], (
            f"argv must be subcommand + args, got: {captured_argv}"
        )
    finally:
        del sys.modules["revue_skill.skill.local_run"]


def test_cli_local_run_shows_help_when_no_args(capsys):
    # Goes through cli.main() (the full argparse path) so argparse cannot
    # intercept --help before our handler runs (REVUE-369 M5).
    from revue_skill.cli import main

    code = main(["local-run"])
    captured = capsys.readouterr()
    assert code == 0
    assert "usage: revue local-run" in captured.out
    assert "position" in captured.out


def test_cli_local_run_shows_help_for_dash_h_via_main(capsys):
    # REVUE-369 M5: argparse must NOT intercept --help. add_help=False on the
    # local-run subparser routes -h/--help through to cmd_local_run.
    from revue_skill.cli import main

    code = main(["local-run", "--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "usage: revue local-run" in captured.out
    assert "position" in captured.out
    assert "prepare" in captured.out


def test_cli_cmd_local_run_strips_argparse_separator():
    # Arrange — sub_args starts with "--" (argparse separator)
    import argparse
    import types
    from unittest.mock import patch

    from revue_skill.cli import cmd_local_run

    fake_mod = types.ModuleType("revue_skill.skill.local_run")
    captured_argv = []

    def fake_main(argv=None):
        captured_argv.append(argv)
        return 0

    fake_mod.main = fake_main
    sys.modules["revue_skill.skill.local_run"] = fake_mod

    try:
        args = argparse.Namespace(sub_args=["--", "position", "--all"])

        # Act
        cmd_local_run(args)

        # Assert — leading "--" is stripped before dispatch
        assert captured_argv == [["position", "--all"]], (
            f"Leading '--' must be stripped, got argv: {captured_argv}"
        )
    finally:
        del sys.modules["revue_skill.skill.local_run"]

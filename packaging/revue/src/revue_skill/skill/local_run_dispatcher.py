"""Dispatcher for revue local-run subcommands.

This module bridges the CLI (revue local-run <subcommand>) to the compiled
local_run module, which contains the actual pipeline implementation.

The compiled local_run module is imported by name, not by path, so it works
both in source-tree (where it's at revue_skill/skill/local_run.py) and in
installed wheels (where it's at revue_skill/skill/local_run.cpython-*.so).

REVUE-369 F4+F6: This dispatcher replaces the source-tree script path
($REPO/scripts/local_run.py) with an installed wheel entry point.
"""

from __future__ import annotations

import sys


def dispatch_local_run(subcommand: str, args: list[str]) -> int:
    """Forward a local-run subcommand to the compiled local_run module.

    Args:
        subcommand: The subcommand name (position, prepare, etc.)
        args: Command-line arguments for the subcommand

    Returns:
        The exit code from the subcommand handler.
    """
    try:
        # Import the compiled local_run module (works in wheels)
        from revue_skill.skill import local_run as lr_module
    except ImportError as exc:
        print(
            f"error: could not import local_run module: {exc}",
            file=sys.stderr,
        )
        return 1

    # local_run.main(argv) parses argv directly. The subcommand is the first
    # positional arg; the rest are its options.
    argv = [subcommand] + args

    try:
        return lr_module.main(argv=argv)
    except SystemExit as exc:
        # argparse calls sys.exit() for --help (0) and parse errors (2).
        # sys.exit() / sys.exit(None) means success — translate to 0, not 1.
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # sys.exit("error message") — print the message and return 1
        print(str(code), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: local-run {subcommand} failed: {exc}", file=sys.stderr)
        return 1

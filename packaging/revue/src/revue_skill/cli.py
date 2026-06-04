"""revue CLI entry point.

Subcommands:

* ``install-skill`` — copy the bundled skill into ``~/.claude/skills/revue``.
* ``verify`` — fetch the release manifest and print the current version info.
* ``version`` — print the installed version.

Network access is required for ``verify``. ``install-skill --skip-verify`` is
provided for air-gapped installs but is not the default.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from . import __version__
from .activate import activate as activate_licence
from .install import DEFAULT_SKILLS_DIR, install
from .manifest import ManifestError, validate
from .skill.local_run_dispatcher import dispatch_local_run
from .support import support_footer

DEFAULT_MANIFEST_URL = "https://revue.sh/skills/manifest.json"


class ManifestURLError(ValueError):
    """Raised when ``--manifest-url`` is not a safe https URL."""


def _validate_manifest_url(url: str) -> None:
    # The noqa: S310 below assumes a trusted https endpoint; enforce that the
    # supplied URL is in fact https with a host, so a wrapper script cannot
    # redirect the fetch at file://, http://, or a missing-host edge case.
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ManifestURLError(
            f"manifest URL must use https (got scheme={parsed.scheme!r}): {url}"
        )
    if not parsed.netloc:
        raise ManifestURLError(f"manifest URL is missing a host: {url}")


def _fetch_manifest(url: str) -> dict:
    _validate_manifest_url(url)
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        return json.load(resp)


def cmd_install_skill(args: argparse.Namespace) -> int:
    target_dir = Path(args.target_dir).expanduser()

    if not args.skip_verify:
        try:
            manifest = _fetch_manifest(args.manifest_url)
            validate(manifest)
        except ManifestError as exc:
            print(f"manifest invalid: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"could not fetch manifest from {args.manifest_url}: {exc}", file=sys.stderr)
            print("re-run with --skip-verify to install without verification.", file=sys.stderr)
            return 2

        if not args.no_strict_version and manifest["current_version"] != __version__:
            print(
                f"installed wheel is {__version__} but manifest advertises {manifest['current_version']} — "
                "upgrade with `pip install --upgrade revue` before installing the skill.",
                file=sys.stderr,
            )
            return 3
        print(f"manifest OK ({manifest['current_version']})")

    result = install(target_dir=target_dir, overwrite=args.overwrite)
    print(f"installed skill at {result.skill_dir} ({result.files_written} files)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        manifest = _fetch_manifest(args.manifest_url)
        validate(manifest)
    except ManifestError as exc:
        print(f"manifest invalid: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"could not fetch manifest from {args.manifest_url}: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(manifest, indent=2))
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    # REVUE-360 AC3: surface the platform from the single source of truth
    # (revue_core.platform_support) so a source/editable install on an
    # unsupported box is visible rather than silently mis-run.
    import platform

    from revue_core.platform_support import (
        format_platform_status_line,
        is_supported,
        unsupported_message,
    )

    system, machine = platform.system(), platform.machine()
    print(format_platform_status_line(system, machine))
    if not is_supported(system, machine):
        print(unsupported_message(system, machine), file=sys.stderr)
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    """REVUE-277: exchange a licence key for a signed JWT and write it
    to ``~/.config/revue/licence.jwt``. See ``activate.py`` for the
    documented exit codes and error envelope."""
    return activate_licence(args.key)


_LOCAL_RUN_HELP = """\
usage: revue local-run <subcommand> [args...]

Subcommands:
  position                       Run position fixtures (use --all for full suite)
  prepare                        Build job JSON for the four reviewer agents
  classify-and-build-vex-jobs    Classify findings and build Vex verifier jobs
  apply-verdicts-and-finalize    Apply Vex verdicts and render final findings
  run                            Legacy: prepare + consolidate (no agents run)

Run `revue local-run <subcommand> --help` for subcommand-specific help.
"""


def cmd_local_run(args: argparse.Namespace) -> int:
    """REVUE-369 F4+F6: dispatch local-run subcommands to the compiled local_run module.

    This command replaces the source-tree invocation ``$REPO/scripts/local_run.py``
    with an installed wheel entry point. Subcommands (position, prepare, etc.)
    are forwarded to the compiled module.
    """
    sub_args = list(getattr(args, "sub_args", []) or [])

    # Strip leading `--` argparse separator if present
    if sub_args and sub_args[0] == "--":
        sub_args = sub_args[1:]

    # Show the subcommand catalog when no subcommand is given or user asks for help
    if not sub_args or sub_args[0] in ("-h", "--help"):
        print(_LOCAL_RUN_HELP)
        return 0

    subcommand = sub_args[0].strip()
    if not subcommand:
        print("error: local-run subcommand cannot be empty", file=sys.stderr)
        return 2

    sub_argv = sub_args[1:]
    return dispatch_local_run(subcommand, sub_argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revue")
    sub = parser.add_subparsers(dest="cmd", required=True)

    install_p = sub.add_parser("install-skill", help="install the bundled Claude Code skill")
    install_p.add_argument(
        "--target-dir",
        default=str(DEFAULT_SKILLS_DIR),
        help="parent dir of the skill (default: ~/.claude/skills)",
    )
    install_p.add_argument(
        "--manifest-url",
        default=DEFAULT_MANIFEST_URL,
        help="URL of the release manifest",
    )
    install_p.add_argument("--overwrite", action="store_true", default=True)
    install_p.add_argument("--no-overwrite", dest="overwrite", action="store_false")
    install_p.add_argument(
        "--skip-verify",
        action="store_true",
        help="install without fetching the manifest (not recommended)",
    )
    install_p.add_argument(
        "--no-strict-version",
        action="store_true",
        help="allow installing when the manifest version differs from the wheel version",
    )
    install_p.set_defaults(func=cmd_install_skill)

    verify_p = sub.add_parser("verify", help="fetch and print the release manifest")
    verify_p.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    verify_p.set_defaults(func=cmd_verify)

    version_p = sub.add_parser("version", help="print the installed version")
    version_p.set_defaults(func=cmd_version)

    activate_p = sub.add_parser(
        "activate",
        help="activate a licence key (REVUE-277): writes ~/.config/revue/licence.jwt",
    )
    activate_p.add_argument("key", help="your licence key (lic_…)")
    activate_p.set_defaults(func=cmd_activate)

    # add_help=False so the outer argparse doesn't intercept `-h/--help` —
    # we route it to cmd_local_run which prints the subcommand catalog.
    # nargs=argparse.REMAINDER captures all remaining args, including
    # `--help` for inner subcommands.
    local_run_p = sub.add_parser(
        "local-run",
        help="run local Revue pipeline (position, prepare, run, etc.)",
        add_help=False,
    )
    local_run_p.add_argument(
        "sub_args",
        nargs=argparse.REMAINDER,
        help="subcommand and arguments (position, prepare, run, classify-and-build-vex-jobs, apply-verdicts-and-finalize)",
    )
    local_run_p.set_defaults(func=cmd_local_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv_list = list(argv)

    # Intercept `revue local-run [-h|--help|<subcommand> ...]` BEFORE argparse,
    # so argparse's add_help can't claim --help and short-circuit our custom
    # subcommand catalog. (REVUE-369 M5: argparse intercepts --help via the
    # outer parser before nargs=REMAINDER on the local-run subparser can
    # capture it. Pre-routing keeps the help and subcommand path coherent.)
    if argv_list and argv_list[0] == "local-run":
        ns = argparse.Namespace(
            cmd="local-run",
            sub_args=argv_list[1:],
            func=cmd_local_run,
        )
        try:
            code = int(ns.func(ns))
        except Exception as exc:  # noqa: BLE001
            print(f"error: `local-run` failed: {exc}", file=sys.stderr)
            print(support_footer(), file=sys.stderr)
            return 1
        if code != 0:
            print(support_footer(), file=sys.stderr)
        return code

    # REVUE-360: `revue --version` / `-V` is the installer's final verify step,
    # but the outer parser uses required subcommands (dest="cmd", required=True),
    # so a bare `--version` would error with exit 2. Pre-route it to cmd_version
    # — same interception pattern as `local-run` above — so it prints the version
    # + platform line and exits 0, consistent with the `version` subcommand.
    if argv_list and argv_list[0] in ("--version", "-V"):
        return cmd_version(argparse.Namespace())

    parser = build_parser()
    args = parser.parse_args(argv_list)
    # REVUE-359: the CLI boundary is the single point that surfaces the support
    # contact, so every activation/install failure — whether it returns a
    # non-zero exit code or raises uncaught (e.g. install() hitting a read-only
    # skills dir) — points the user at support. Individual subcommands stay
    # free of footer plumbing.
    try:
        code = int(args.func(args))
    except Exception as exc:  # noqa: BLE001 — last-resort boundary handler.
        # Catches only ``Exception``; BaseException subclasses
        # (KeyboardInterrupt, SystemExit) pass through intentionally so Ctrl-C
        # and explicit exits keep their native behaviour and are NOT remapped to
        # exit 1 or given a support footer. A future maintainer adding a custom
        # BaseException subclass must opt it in here deliberately.
        print(f"error: `{args.cmd}` failed: {exc}", file=sys.stderr)
        print(support_footer(), file=sys.stderr)
        return 1
    if code != 0:
        print(support_footer(), file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

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
from .install import DEFAULT_SKILLS_DIR, install
from .manifest import ManifestError, validate

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
    return 0


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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

#!/usr/bin/env python3
"""Revue CLI — local diff review, config init, and validation.

Entry point registered as ``revue`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from revue.core.config_loader import (
    DEFAULT_REVUE_YML,
    load_config,
    validate_config,
)
from revue.core.diff_parser import filter_changes, parse_diff_file
from revue.core.ai_client import create_ai_client
from revue.core.pipeline import ReviewPipeline


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revue",
        description="Revue — AI-powered code review",
    )
    sub = parser.add_subparsers(dest="command")

    # -- review --
    review = sub.add_parser("review", help="Review a local diff file")
    review.add_argument("--diff", required=True, help="Path to .diff file")
    review.add_argument("--config", default=".revue.yml", help="Path to config file")
    review.add_argument(
        "--provider",
        choices=["anthropic", "openai", "azure", "openrouter", "custom"],
        help="Override AI provider",
    )
    review.add_argument("--model", help="Override model string")
    review.add_argument(
        "--output",
        choices=["markdown", "json", "text"],
        default=None,
        help="Output format (default: markdown)",
    )
    review.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse diff and validate config without calling AI",
    )
    review.set_defaults(func=cmd_review)

    # -- init --
    init = sub.add_parser("init", help="Scaffold a .revue.yml in current directory")
    init.add_argument(
        "--force", action="store_true", help="Overwrite existing .revue.yml"
    )
    init.set_defaults(func=cmd_init)

    # -- validate --
    val = sub.add_parser("validate", help="Validate a config file")
    val.add_argument("--config", default=".revue.yml", help="Path to config file")
    val.set_defaults(func=cmd_validate)

    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_review(
    args: argparse.Namespace,
    pipeline_factory: Callable | None = None,
) -> int:
    """Run a code review.  Accepts an optional *pipeline_factory* for DIP injection."""
    # 1. Verify diff file exists
    diff_path = Path(args.diff)
    if not diff_path.exists():
        print(f"Error: diff file not found: {args.diff}", file=sys.stderr)
        return 1

    # 2. Build overrides from CLI flags
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.output:
        overrides["output_format"] = args.output

    # 3. Load config
    try:
        config = load_config(config_path=args.config, overrides=overrides)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # 4. Validate config
    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        return 1

    # 5. Dry-run: parse, filter, list files and exit (no AI call)
    if args.dry_run:
        try:
            changes = parse_diff_file(str(diff_path))
        except Exception as exc:
            print(f"Error parsing diff: {exc}", file=sys.stderr)
            return 1

        included, excluded = filter_changes(
            changes, config.ignore_patterns, config.max_diff_lines
        )
        total = len(changes)
        print(f"Found {total} files ({len(excluded)} excluded by filters)")
        for fc in included:
            print(f"  [review] {fc.file_path} (+{fc.additions}/-{fc.deletions})")
        for fc in excluded:
            print(f"  [skip]   {fc.file_path}")
        return 0

    # 6. Resolve API key — fail fast
    try:
        config.resolve_api_key()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # 7. Build pipeline (DIP: injected factory or default)
    try:
        if pipeline_factory is not None:
            pipeline = pipeline_factory(config)
        else:
            pipeline = ReviewPipeline(config)
    except Exception as exc:
        print(f"Error creating AI client: {exc}", file=sys.stderr)
        return 1

    # 8. Run pipeline
    review_results, excluded = pipeline.run(str(diff_path))
    total = len(review_results) + len(excluded)
    print(f"Found {total} files ({len(excluded)} excluded by filters)")

    # 9. Output
    results: list[dict[str, str]] = []
    for rr in review_results:
        if rr.error:
            print(f"Error reviewing {rr.file_path}: {rr.error}", file=sys.stderr)
            results.append({"file": rr.file_path, "review": f"ERROR: {rr.error}"})
        else:
            results.append({"file": rr.file_path, "review": rr.response})

    fmt = config.output_format
    if fmt == "json":
        print(json.dumps(results, indent=2))
    elif fmt == "text":
        for r in results:
            print(f"--- {r['file']} ---")
            print(r["review"])
            print()
    else:
        # markdown (default)
        for r in results:
            print(f"## {r['file']}\n")
            print(r["review"])
            print()

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(".revue.yml")
    if target.exists() and not args.force:
        print(
            "Error: .revue.yml already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    target.write_text(DEFAULT_REVUE_YML)
    print("Created .revue.yml")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(config_path=args.config)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    errors = validate_config(config)
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        return 1

    print("Config valid")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args) or 0)

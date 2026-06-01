#!/usr/bin/env python3
"""Copy source-of-truth files into the revue wheel build tree.

Run from the repo root before building the wheel::

    python packaging/revue/tools/vendor_sources.py

The mapping lives in ``packaging/revue/tools/sources.yaml``. For files
that need import-path rewrites (``from revue.* …`` becomes
``from revue_skill.vendored.* …``), the rewrites are declared per entry and
applied as plain string substitutions on the copy — the source-of-truth is
never modified.

A companion test (``tests/test_vendored_sources_in_sync.py``) re-runs the
vendoring into a temp dir and asserts byte-equivalence against the in-tree
copy. CI fails on drift.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
SOURCES_YAML = PACKAGING_DIR / "tools" / "sources.yaml"

# First-class files in src/revue_skill/skill/ — git-tracked, listed in
# build_nuitka.COMPILE_ROOTS, imported at startup. The --clean sweep must
# preserve these even if sources.yaml never mentions them. Adding a new
# first-class module to skill/ requires updating BOTH this set and
# COMPILE_ROOTS. The test in test_vendor_clean_preserves_first_class_files.py
# guards against drift.
FIRST_CLASS_SKILL_FILES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "cache_paths.py",
        "cost_footer.py",
        "emit_usage.py",
        "local_run_dispatcher.py",
        "post_review_signals.py",
        "update_usage_cache.py",
        "upgrade_prompt.py",
    }
)


class UnsafePathError(ValueError):
    """Raised when a sources.yaml entry resolves outside its allowed base dir."""


class RewriteNotFoundError(ValueError):
    """Raised when a rewrite_imports ``from`` literal is absent from the source.

    A ``str.replace`` whose ``from`` is missing is a silent no-op, so source
    drift would ship unrewritten code (e.g. the REVUE-370 licence bypass).
    Failing loudly turns that into a hard CI error at vendor time.
    """


def _safe_join(base: Path, rel: str) -> Path:
    # sources.yaml is committed, but defence-in-depth: refuse absolute paths
    # and any join that escapes the base after resolution, so a tampered
    # manifest can't redirect copies to /etc or overwrite arbitrary files.
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise UnsafePathError(f"absolute path not allowed in sources.yaml: {rel}")
    resolved = (base / rel_path).resolve()
    base_resolved = base.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise UnsafePathError(
            f"path {rel!r} resolves outside {base_resolved} (→ {resolved})"
        )
    return resolved


@dataclass(frozen=True)
class FileEntry:
    source: Path
    target: Path
    rewrites: tuple[tuple[str, str], ...]


def _load_entries() -> list[FileEntry]:
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    entries: list[FileEntry] = []
    for section in ("skill", "vendored", "agent_prompts"):
        for raw in data.get(section, []):
            if "source_dir" in raw:
                entries.extend(_expand_dir(raw))
            else:
                entries.append(_file_entry(raw))
    return entries


def _expand_dir(raw: dict) -> Iterable[FileEntry]:
    src_dir = _safe_join(REPO_ROOT, raw["source_dir"])
    tgt_dir = _safe_join(PACKAGING_DIR, raw["target_dir"])
    if not src_dir.is_dir():
        raise FileNotFoundError(f"source dir {src_dir} does not exist")
    for src_file in src_dir.rglob("*"):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src_dir)
        yield FileEntry(
            source=src_file,
            target=tgt_dir / rel,
            rewrites=(),
        )


def _file_entry(raw: dict) -> FileEntry:
    rewrites = tuple(
        (item["from"], item["to"]) for item in raw.get("rewrite_imports", [])
    )
    return FileEntry(
        source=_safe_join(REPO_ROOT, raw["source"]),
        target=_safe_join(PACKAGING_DIR, raw["target"]),
        rewrites=rewrites,
    )


def _apply(entry: FileEntry) -> None:
    if not entry.source.is_file():
        raise FileNotFoundError(f"source {entry.source} does not exist")
    entry.target.parent.mkdir(parents=True, exist_ok=True)

    if entry.rewrites:
        text = entry.source.read_text(encoding="utf-8")
        for find, replace in entry.rewrites:
            if find not in text:
                raise RewriteNotFoundError(
                    f"rewrite_imports rule for {entry.target.name} did not match "
                    f"{entry.source}: the `from` literal is absent, so the rewrite "
                    f"would silently no-op and ship unrewritten source. The source "
                    f"text has likely drifted from sources.yaml.\n  from: {find!r}"
                )
            text = text.replace(find, replace)
        entry.target.write_text(text, encoding="utf-8")
    else:
        shutil.copy2(entry.source, entry.target)


VENDORED_INIT_BODY = (
    '"""Vendored copies of revue source-of-truth modules.\n'
    "\n"
    "Do not edit files in this directory by hand. They are regenerated by\n"
    "``tools/vendor_sources.py`` from the canonical sources in ``src/revue/`` and\n"
    "``scripts/positioning/``. ``tests/test_vendored_sources_in_sync.py`` fails CI\n"
    "on drift.\n"
    '"""\n'
)


def _clean_vendored_targets() -> None:
    """Remove vendored content from the build tree while preserving first-class files.

    REVUE-369 F1: skill/ contains first-class files (cache_paths.py,
    update_usage_cache.py, post_review_signals.py, etc.) that are git-tracked,
    listed in COMPILE_ROOTS, and imported at startup. They are NEVER vendored
    and must NEVER be deleted.

    REVUE-369 M4: stale top-level vendored files (entries removed from
    sources.yaml since the last build) must still be swept. We do this by
    deleting every file in skill/ top-level that is not in the protected
    FIRST_CLASS_SKILL_FILES set. Subdirectories declared as target_dir get
    rmtree'd to wipe any internal stragglers.
    """
    # Always wipe vendored/ — every file in it is vendored, no first-class.
    vendored_dir = PACKAGING_DIR / "src/revue_skill/vendored"
    if vendored_dir.exists():
        shutil.rmtree(vendored_dir)

    # Sweep top-level files in skill/ that are NOT first-class (removes any
    # stale top-level vendored file even if sources.yaml no longer mentions it).
    skill_dir = PACKAGING_DIR / "src/revue_skill/skill"
    if skill_dir.is_dir():
        for entry in skill_dir.iterdir():
            if entry.is_file() and entry.name not in FIRST_CLASS_SKILL_FILES:
                entry.unlink()

    # Wipe skill/_revue/ wholesale — every file under it is vendored (agent
    # prompts, config, models_registry). Wiping the entire subtree handles
    # the case where a target_dir is removed from sources.yaml entirely
    # (REVUE-369 M7: M4's per-target_dir cleanup left stale subdirs).
    revue_subdir = skill_dir / "_revue"
    if revue_subdir.exists():
        shutil.rmtree(revue_subdir)


def vendor(*, clean: bool = False, dry_run: bool = False) -> list[FileEntry]:
    """Copy all configured sources into the wheel build tree."""
    if clean:
        _clean_vendored_targets()

    entries = _load_entries()

    if not dry_run:
        for entry in entries:
            _apply(entry)
        # Synthesise a marker __init__.py so revue_skill.vendored is a package.
        # No source-of-truth equivalent — this lives only in the wheel build tree.
        init_path = PACKAGING_DIR / "src/revue_skill/vendored/__init__.py"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        init_path.write_text(VENDORED_INIT_BODY, encoding="utf-8")

    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="remove the target trees first")
    parser.add_argument("--dry-run", action="store_true", help="report files without writing")
    args = parser.parse_args(argv)

    entries = vendor(clean=args.clean, dry_run=args.dry_run)
    for entry in entries:
        src = entry.source.relative_to(REPO_ROOT)
        tgt = entry.target.relative_to(PACKAGING_DIR)
        print(f"{src} -> {tgt}")
    print(f"vendored {len(entries)} file(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

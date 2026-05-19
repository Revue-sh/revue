"""Install the bundled Claude Code skill into the user's skills directory.

After ``pip install revue`` the user runs ``revue install-skill``:

1. Resolve the bundled skill directory (``revue_skill/skill``) inside the wheel.
2. Resolve the destination — defaults to ``~/.claude/skills/revue``.
3. Copy ``SKILL.md`` and the bundled orchestrator into the destination.
4. Print the path for the user to confirm.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


DEFAULT_SKILLS_DIR = Path.home() / ".claude" / "skills"
SKILL_NAME = "revue"


@dataclass(frozen=True)
class InstallResult:
    """Where the skill was installed and how many files were written."""

    skill_dir: Path
    files_written: int


def bundled_skill_root() -> Path:
    """Return the on-disk path to the bundled skill directory."""
    root = files("revue_skill").joinpath("skill")
    return Path(str(root))


def install(target_dir: Path = DEFAULT_SKILLS_DIR, *, overwrite: bool = True) -> InstallResult:
    """Copy the bundled skill into ``target_dir/SKILL_NAME``."""
    src = bundled_skill_root()
    dst = target_dir / SKILL_NAME

    if dst.exists():
        if not overwrite:
            raise FileExistsError(
                f"{dst} already exists — re-run with --overwrite to replace it",
            )
        shutil.rmtree(dst)

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    files_written = sum(1 for _ in dst.rglob("*") if _.is_file())
    return InstallResult(skill_dir=dst, files_written=files_written)

"""Tests for build/determine_release.py — commit-message and file-based bump logic."""
import subprocess
import sys
from pathlib import Path

import pytest

BUILD_DIR = Path(__file__).resolve().parents[2] / "build"
SCRIPT = BUILD_DIR / "determine_release.py"


def bump(commit_msg: str, changed_files: list[str] | None = None) -> str:
    """Call determine_release.py as a subprocess and return its stdout."""
    args = [sys.executable, str(SCRIPT), commit_msg] + (changed_files or [])
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout.strip()


# ── existing message-based behaviour (must not regress) ───────────────────────

class TestMessageBump:
    def test_feat_returns_minor(self):
        assert bump("feat(web): add dashboard") == "minor"

    def test_fix_returns_patch(self):
        assert bump("fix(api): correct header validation") == "patch"

    def test_perf_returns_patch(self):
        assert bump("perf(core): cache licence response") == "patch"

    def test_refactor_returns_patch(self):
        assert bump("refactor(db): extract query builder") == "patch"

    def test_chore_returns_none(self):
        assert bump("chore(ci): update runner image") == "none"

    def test_docs_returns_none(self):
        assert bump("docs(readme): fix typo") == "none"

    def test_breaking_exclamation_returns_major(self):
        assert bump("feat(api)!: drop v1 endpoints") == "major"

    def test_breaking_change_in_body_returns_major(self):
        assert bump("fix(auth): rotate keys\n\nBREAKING CHANGE: all sessions invalidated") == "major"

    def test_no_match_returns_none(self):
        assert bump("WIP: sketching ideas") == "none"

    def test_ticket_in_commit_does_not_affect_bump(self):
        assert bump("fix(skill)[REVUE-435]: remove position mode") == "patch"


# ── file-based guard: packaging/ changes upgrade none → patch ─────────────────

class TestFileGuard:
    def test_chore_with_packaging_file_triggers_patch(self):
        assert bump(
            "chore(skill)[REVUE-435]: update SKILL.md",
            ["packaging/revue/src/revue_skill/skill/SKILL.md"],
        ) == "patch"

    def test_chore_with_multiple_packaging_files_triggers_patch(self):
        assert bump(
            "chore(ci): tweak build script",
            ["packaging/revue_core/build/build_nuitka.py", "packaging/revue/pyproject.toml"],
        ) == "patch"

    def test_chore_with_only_docs_files_stays_none(self):
        assert bump(
            "chore(docs): update README",
            ["docs/guides/testing.md", "docs/team/PR_TEMPLATE_GUIDE.md"],
        ) == "none"

    def test_chore_with_only_claude_files_stays_none(self):
        assert bump(
            "chore(tooling): update skill",
            [".claude/skills/revue/SKILL.md"],
        ) == "none"

    def test_chore_with_mixed_files_triggers_patch(self):
        """packaging/ file among others must trigger patch."""
        assert bump(
            "chore(release): update docs and skill",
            ["docs/readme.md", "packaging/revue/src/revue_skill/skill/SKILL.md"],
        ) == "patch"

    def test_feat_with_packaging_files_stays_minor(self):
        """feat is already >= patch; file guard must not downgrade it."""
        assert bump(
            "feat(skill): add new review mode",
            ["packaging/revue/src/revue_skill/skill/SKILL.md"],
        ) == "minor"

    def test_fix_with_packaging_files_stays_patch(self):
        """fix already produces patch; file guard must not change it."""
        assert bump(
            "fix(skill): correct routing",
            ["packaging/revue/src/revue_skill/skill/SKILL.md"],
        ) == "patch"

    def test_breaking_with_packaging_files_stays_major(self):
        """major must not be downgraded by file guard logic."""
        assert bump(
            "feat(api)!: breaking change",
            ["packaging/revue_core/build/build_nuitka.py"],
        ) == "major"

    def test_no_files_argument_preserves_message_bump(self):
        """Passing no files must not change the message-only result."""
        assert bump("chore(ci): cleanup", []) == "none"
        assert bump("feat(web): new page", []) == "minor"

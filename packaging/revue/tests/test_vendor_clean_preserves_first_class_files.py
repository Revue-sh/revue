"""REVUE-369 F1: vendor_sources.py --clean must not destroy first-class skill files.

The vendor() function with clean=True destroys both skill/ and vendored/
directories. However, skill/ contains first-class files (cache_paths.py,
update_usage_cache.py, post_review_signals.py) that are:
- Git-tracked in src/revue_skill/skill/
- Listed in COMPILE_ROOTS in build_nuitka.py
- Imported by local_run.py at module startup

If vendor_sources.py --clean wipes them, the subsequent rebuild has no
source to re-copy them from, so they vanish. The fix is to narrow the
rmtree to vendored/ only.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = PACKAGING_DIR / "tools"
BUILD_DIR = PACKAGING_DIR / "build"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(BUILD_DIR) not in sys.path:
    sys.path.insert(0, str(BUILD_DIR))

import build_nuitka  # noqa: E402
import vendor_sources as vs  # noqa: E402


def test_vendor_clean_preserves_first_class_skill_files(tmp_path, monkeypatch):
    # Arrange — set up a fake PACKAGING_DIR with both files
    packaging = tmp_path / "packaging"
    packaging.mkdir()

    skill_dir = packaging / "src" / "revue_skill" / "skill"
    skill_dir.mkdir(parents=True)
    first_class_py = skill_dir / "cache_paths.py"
    first_class_py.write_text("# First-class module imported by local_run.py\nprint('hello')\n")

    vendored_dir = packaging / "src" / "revue_skill" / "vendored"
    vendored_dir.mkdir(parents=True)
    vendored_py = vendored_dir / "position_adapter.py"
    vendored_py.write_text("# Copied from src/revue/ — OK to delete and re-generate\npass\n")

    monkeypatch.setattr(vs, "PACKAGING_DIR", packaging)

    # Act — call vendor() with clean=True (simulate the build pipeline)
    # (We mock-pass entries to avoid needing sources.yaml; just test the clean logic)
    def fake_load_entries():
        return [
            vs.FileEntry(
                source=Path("/fake/source.py"),
                target=vendored_dir / "position_adapter.py",
                rewrites=(),
            )
        ]

    monkeypatch.setattr(vs, "_load_entries", fake_load_entries)

    # The vendor() call will try to re-apply entries, but we're focused on
    # the clean step. We'll prevent _apply() from running to isolate the
    # clean logic.
    def no_op_apply(entry):
        pass

    monkeypatch.setattr(vs, "_apply", no_op_apply)

    vs.vendor(clean=True, dry_run=False)

    # Assert — vendored was cleared (yes, gone), but first-class still exists
    assert not vendored_py.exists(), "vendored/ should be wiped so fresh copies are re-downloaded"
    assert first_class_py.exists(), (
        "skill/ contains git-tracked files imported at startup (cache_paths.py, "
        "update_usage_cache.py, post_review_signals.py). They must NOT be deleted. (F1)"
    )
    assert first_class_py.read_text() == "# First-class module imported by local_run.py\nprint('hello')\n"


def test_first_class_skill_files_matches_compiled_minus_vendored():
    # REVUE-369 M4: a "first-class" file in skill/ is one that is compiled
    # (build_nuitka.COMPILE_ROOTS) but NOT vendored from elsewhere
    # (sources.yaml). Those are the modules --clean must preserve.
    #
    # Modules that are BOTH compiled and vendored (e.g. local_run.py — copied
    # from scripts/local_run.py with import rewrites) are DELETED on --clean
    # so the fresh vendoring writes a clean copy.
    #
    # This test catches drift: adding a new first-class skill module without
    # registering it in FIRST_CLASS_SKILL_FILES causes the next --clean to
    # delete it.
    import yaml

    # Arrange — compile_roots view: every skill/ entry in COMPILE_ROOTS
    compiled_in_skill = {
        p.name for p in build_nuitka.COMPILE_ROOTS if p.parent.name == "skill"
    }
    # Vendored-into-skill-top-level view: every sources.yaml entry that targets
    # skill/<file> directly (not skill/_revue/* or other nested paths)
    sources_data = yaml.safe_load(vs.SOURCES_YAML.read_text(encoding="utf-8"))
    vendored_top_level = set()
    for section in ("skill", "vendored", "agent_prompts"):
        for raw in sources_data.get(section, []):
            target = raw.get("target", "")
            # Match exactly src/revue_skill/skill/<file> with no further depth
            if target.startswith("src/revue_skill/skill/") and "/" not in target[len("src/revue_skill/skill/"):]:
                vendored_top_level.add(target.rsplit("/", 1)[-1])

    derived_first_class = compiled_in_skill - vendored_top_level
    # FIRST_CLASS_SKILL_FILES also protects __init__.py (not in COMPILE_ROOTS)
    declared_first_class = vs.FIRST_CLASS_SKILL_FILES - {"__init__.py"}

    # Assert — declared matches derived
    assert declared_first_class == derived_first_class, (
        "vendor_sources.FIRST_CLASS_SKILL_FILES has drifted from "
        "(COMPILE_ROOTS ∩ skill/) − (sources.yaml skill/-top-level targets). "
        f"Declared only: {declared_first_class - derived_first_class}, "
        f"Derived only: {derived_first_class - declared_first_class}. "
        "Update FIRST_CLASS_SKILL_FILES when adding/removing a first-class skill module."
    )


def test_vendor_clean_removes_stale_vendored_files_from_skill_dir(tmp_path, monkeypatch):
    # REVUE-369 M4: when sources.yaml drops a vendored target, the old vendored
    # copy must be cleaned out so it doesn't ship into the wheel.
    #
    # Arrange — set up a fake PACKAGING_DIR with both stale-vendored AND first-class files
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    skill_dir = packaging / "src" / "revue_skill" / "skill"
    skill_dir.mkdir(parents=True)

    # First-class file — must survive
    first_class = skill_dir / "cache_paths.py"
    first_class.write_text("# First-class module\n")

    # Stale vendored file — was in old sources.yaml, no longer listed, must be removed
    stale_vendored = skill_dir / "old_removed_module.py"
    stale_vendored.write_text("# Stale — used to be vendored, removed from sources.yaml\n")

    # Stale vendored directory contents — agent prompt that was removed upstream
    revue_agents = skill_dir / "_revue" / "agents"
    revue_agents.mkdir(parents=True)
    stale_agent = revue_agents / "old_agent.md"
    stale_agent.write_text("# This agent was removed from _revue/agents/ upstream\n")

    # Source-of-truth sources.yaml — only declares the current (NOT stale) targets
    sources_yaml = packaging / "tools" / "sources.yaml"
    sources_yaml.parent.mkdir(parents=True)
    sources_yaml.write_text(
        "skill:\n"
        "  - source: scripts/local_run.py\n"
        "    target: src/revue_skill/skill/local_run.py\n"
        "vendored:\n"
        "  - source: packaging/revue_core/src/revue_core/core/log.py\n"
        "    target: src/revue_skill/vendored/log.py\n"
        "agent_prompts:\n"
        "  - source_dir: _revue/agents\n"
        "    target_dir: src/revue_skill/skill/_revue/agents\n"
    )

    monkeypatch.setattr(vs, "PACKAGING_DIR", packaging)
    monkeypatch.setattr(vs, "REPO_ROOT", repo_root)
    monkeypatch.setattr(vs, "SOURCES_YAML", sources_yaml)
    monkeypatch.setattr(vs, "_apply", lambda entry: None)

    # Source files must exist for _expand_dir() to walk them without erroring
    src_agents = repo_root / "_revue" / "agents"
    src_agents.mkdir(parents=True)
    (src_agents / "current_agent.md").write_text("# Current agent")
    (repo_root / "scripts").mkdir()
    (repo_root / "scripts" / "local_run.py").touch()

    # Act
    vs.vendor(clean=True, dry_run=False)

    # Assert — first-class preserved, stale vendored swept
    assert first_class.exists(), "first-class cache_paths.py must NOT be deleted"
    assert not stale_vendored.exists(), (
        "stale vendored file must be removed so it doesn't ship in the wheel (M4)"
    )
    assert not stale_agent.exists(), (
        "stale agent prompt under skill/_revue/agents/ must be removed so the wheel "
        "doesn't ship outdated copies after they're removed from sources.yaml (M4)"
    )


def test_vendor_clean_wipes_revue_subdir_even_when_target_dir_removed_from_yaml(tmp_path, monkeypatch):
    # REVUE-369 M7 (Codex finding): if a target_dir is removed entirely from
    # sources.yaml (e.g. `_revue/clients` is no longer vendored at all), the
    # M4 fix's per-target_dir wipe never touches it. Stale files survive.
    # Solution: wipe skill/_revue/ wholesale before re-vendoring.
    #
    # Arrange
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    skill_dir = packaging / "src" / "revue_skill" / "skill"
    skill_dir.mkdir(parents=True)

    # First-class file must survive
    first_class = skill_dir / "cache_paths.py"
    first_class.write_text("# First-class\n")

    # Stale _revue/clients/ subtree — target_dir was REMOVED from sources.yaml
    stale_clients_dir = skill_dir / "_revue" / "clients"
    stale_clients_dir.mkdir(parents=True)
    stale_client = stale_clients_dir / "old_client.py"
    stale_client.write_text("# Was vendored, target_dir removed from sources.yaml\n")

    # sources.yaml — no longer declares the _revue/clients target_dir at all
    sources_yaml = packaging / "tools" / "sources.yaml"
    sources_yaml.parent.mkdir(parents=True)
    sources_yaml.write_text(
        "skill: []\n"
        "vendored: []\n"
        "agent_prompts:\n"
        "  - source_dir: _revue/agents\n"
        "    target_dir: src/revue_skill/skill/_revue/agents\n"
    )

    monkeypatch.setattr(vs, "PACKAGING_DIR", packaging)
    monkeypatch.setattr(vs, "REPO_ROOT", repo_root)
    monkeypatch.setattr(vs, "SOURCES_YAML", sources_yaml)
    monkeypatch.setattr(vs, "_apply", lambda entry: None)

    # Source files for _expand_dir
    src_agents = repo_root / "_revue" / "agents"
    src_agents.mkdir(parents=True)
    (src_agents / "agent.md").write_text("# agent")

    # Act
    vs.vendor(clean=True, dry_run=False)

    # Assert — first-class survives, stale _revue/clients/ is gone
    assert first_class.exists(), "first-class file must survive"
    assert not stale_client.exists(), (
        "stale skill/_revue/clients/old_client.py must be removed even when "
        "the target_dir was removed from sources.yaml entirely (M7)"
    )
    assert not stale_clients_dir.exists(), (
        "stale skill/_revue/clients/ subdir must be removed wholesale (M7)"
    )

"""Tests for AC1 (Nuitka compilation), AC2 (prompts plain text), and vendored state."""

from __future__ import annotations

from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
SKILL_ROOT = PACKAGING_DIR / "src" / "revue_skill" / "skill"

REQUIRED_AGENT_PROMPTS = {
    "_revue/agents/zara.md",
    "_revue/agents/kai.md",
    "_revue/agents/maya.md",
    "_revue/agents/leo.md",
    "_revue/agents/cleo.yaml",
    "_revue/agents/nova.yaml",
    "_revue/agents/vex.yaml",
}

# Orchestration Python files that must NOT ship as plain .py in the
# production wheel (AC1). vendor_sources.py copies them into the build
# tree; build_nuitka.py compiles them to .so/.pyd before wheel assembly.
ORCHESTRATION_PY = [
    PACKAGING_DIR / "src" / "revue_skill" / "cli.py",
    PACKAGING_DIR / "src" / "revue_skill" / "install.py",
    PACKAGING_DIR / "src" / "revue_skill" / "manifest.py",
    PACKAGING_DIR / "src" / "revue_skill" / "skill" / "local_run.py",
]


def test_skill_md_is_present() -> None:
    skill_md = SKILL_ROOT / "SKILL.md"
    assert skill_md.is_file(), f"missing {skill_md}"
    content = skill_md.read_text(encoding="utf-8")
    assert content.startswith("---"), "SKILL.md is missing the Claude Code frontmatter block"
    assert "name: revue" in content


def test_all_agent_prompts_bundled() -> None:
    missing = sorted(
        rel for rel in REQUIRED_AGENT_PROMPTS if not (SKILL_ROOT / rel).is_file()
    )
    assert missing == [], f"missing agent prompts in wheel bundle: {missing}"


def test_agent_prompts_are_plain_text() -> None:
    """AC2: agent prompt files in _revue/agents/ must be .md or .yaml, never .py."""
    agents_dir = SKILL_ROOT / "_revue" / "agents"
    for f in agents_dir.rglob("*"):
        if f.is_file():
            assert f.suffix in {".md", ".yaml", ".yml", ".json", ""}, (
                f"unexpected file type in _revue/agents/: {f.relative_to(SKILL_ROOT)}"
            )


def test_orchestration_py_present_in_build_tree() -> None:
    """The build tree must contain the orchestration .py files for Nuitka to compile."""
    for py_file in ORCHESTRATION_PY:
        assert py_file.is_file(), (
            f"orchestration source missing from build tree: {py_file.relative_to(PACKAGING_DIR)}\n"
            "Run: python packaging/revue/tools/vendor_sources.py --clean"
        )


def test_orchestration_py_have_revue_skill_imports() -> None:
    """After vendoring, local_run.py must reference revue_skill.vendored.*, not revue_local.*"""
    local_run = PACKAGING_DIR / "src" / "revue_skill" / "skill" / "local_run.py"
    text = local_run.read_text(encoding="utf-8")
    assert "from revue_skill.vendored.position_adapter" in text
    assert "from revue_skill.vendored.terminal_state" in text
    assert "from revue_skill.vendored.positioning_adapters" in text
    import_lines = [l for l in text.splitlines() if l.strip().startswith(("from ", "import "))]
    stale = [l for l in import_lines if "revue_local" in l]
    assert not stale, (
        "stale revue_local import lines found — re-run vendor_sources.py:\n"
        + "\n".join(stale)
    )


def test_models_registry_bundled() -> None:
    registry = SKILL_ROOT / "_revue" / "models_registry.yml"
    assert registry.is_file()
    assert "deepseek" in registry.read_text(encoding="utf-8").lower()

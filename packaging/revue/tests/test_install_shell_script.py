"""Tests for ``scripts/install.sh`` — the one-command curl-pipe-bash installer.

The installer (REVUE-276 / E-P2A-S2) lives at the repo root under
``scripts/install.sh`` and is fetched by end users via:

    curl -fsSL https://raw.githubusercontent.com/Revue-sh/revue/main/scripts/install.sh | bash

These tests stub ``uv`` and ``pipx`` with bash scripts placed on ``PATH`` and
point ``HOME`` at a temporary directory, so they never touch real PyPI or the
user's environment.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"


def _make_stub(bin_dir: Path, name: str, exit_code: int = 0, log_path: Path | None = None) -> None:
    """Write a bash stub at ``bin_dir/name`` that logs invocations and exits ``exit_code``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = log_path or (bin_dir / f"{name}.log")
    script = bin_dir / name
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "{name} $*" >> "{log}"
            exit {exit_code}
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_revue_stub(bin_dir: Path, *, claude_skills_dir: Path) -> Path:
    """Stub the ``revue`` CLI so it simulates ``install-skill`` writing files.

    The stub logs every invocation (including ``--target-dir`` so AC4 can be
    asserted from the log) and honours ``--target-dir`` by writing the skill
    files under the *parsed* parent dir rather than a hardcoded location. When
    ``--target-dir`` is absent it falls back to ``claude_skills_dir`` so the
    global-scope tests keep working unchanged.
    """
    log = bin_dir / "revue.log"
    script = bin_dir / "revue"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "revue $*" >> "{log}"
            if [ "$1" = "install-skill" ]; then
                target="{claude_skills_dir}"
                # Parse --target-dir <dir> out of the arguments so the stub
                # writes the skill where the installer asked it to.
                prev=""
                for arg in "$@"; do
                    if [ "$prev" = "--target-dir" ]; then
                        target="$arg"
                    fi
                    prev="$arg"
                done
                mkdir -p "$target/revue"
                printf '# revue skill\\n' > "$target/revue/SKILL.md"
            fi
            exit 0
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return log


def _run_installer(
    *,
    env: dict[str, str],
    cwd: Path,
    args: list[str] | None = None,
    detach_tty: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["bash", str(INSTALL_SCRIPT), *(args or [])]
    return subprocess.run(  # noqa: S603 — controlled script, controlled env
        cmd,
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        # detach from the controlling terminal so ``/dev/tty`` cannot be opened —
        # this deterministically exercises the AC7 no-tty fallback even when the
        # test suite is launched from an interactive shell. (capture_output alone
        # only redirects stdout/stderr; the child still inherits the tty.)
        start_new_session=detach_tty,
    )


@pytest.fixture()
def installer_env(tmp_path: Path) -> dict[str, Path | str]:
    """A clean, hermetic environment for running the installer."""
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    workspace = tmp_path / "workspace"
    home.mkdir()
    bin_dir.mkdir()
    workspace.mkdir()

    claude_dir = home / ".claude"
    claude_dir.mkdir()  # signal: Claude Code is "installed"

    # REVUE-354 finding #4: detection now gates on the `claude` host CLI being on
    # PATH (not the ~/.claude dir), so the fixture stubs it for the success path.
    _make_stub(bin_dir, "claude")

    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "REVUE_INSTALL_NONINTERACTIVE": "1",
    }
    return {
        "env": env,
        "home": home,
        "bin": bin_dir,
        "workspace": workspace,
        "claude_dir": claude_dir,
    }


def _make_uname_stub(bin_dir: Path, *, system: str, machine: str) -> None:
    """Stub ``uname`` so the platform guard sees a chosen ``-s``/``-m`` pair.

    Shadows the real ``uname`` on PATH; only the installer's platform guard
    consults it, so the rest of the script still uses the real OS for home-dir
    resolution etc.
    """
    script = bin_dir / "uname"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            case "$1" in
              -s) echo "{system}" ;;
              -m) echo "{machine}" ;;
              *) echo "{system} {machine}" ;;
            esac
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_install_script_exists_and_is_executable():
    # Arrange / Act
    exists = INSTALL_SCRIPT.exists()
    mode = INSTALL_SCRIPT.stat().st_mode if exists else 0

    # Assert — installer present and runnable
    assert exists, f"missing installer script: {INSTALL_SCRIPT}"
    assert mode & stat.S_IXUSR, "installer script must be executable (chmod +x)"


def test_install_prefers_uv_tool_install_when_uv_present(installer_env):
    # Arrange — uv on PATH, pipx absent
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — uv was called with ``tool install --force revue`` and pipx never invoked
    assert result.returncode == 0, result.stderr
    uv_log = (bin_dir / "uv.log").read_text()
    assert "tool install" in uv_log
    assert "revue" in uv_log
    assert "--force" in uv_log, "must pass --force so re-runs upgrade in place"
    assert not (bin_dir / "pipx.log").exists()


def test_install_falls_back_to_pipx_when_uv_missing(installer_env):
    # Arrange — only pipx on PATH
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_stub(bin_dir, "pipx")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — pipx invoked with ``install --force revue``
    assert result.returncode == 0, result.stderr
    pipx_log = (bin_dir / "pipx.log").read_text()
    assert "install" in pipx_log
    assert "revue" in pipx_log
    assert "--force" in pipx_log


def test_install_installs_skill_and_writes_no_command_file(installer_env):
    # Arrange — Claude Code present, uv available, revue stubbed
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — the bundled skill is the sole source of /revue: skill installed,
    # NO command-file shim written (it would collide with the skill on /revue).
    assert result.returncode == 0, result.stderr
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists(), (
        "the bundled skill must be installed (it provides /revue)"
    )
    assert not (home / ".claude" / "commands" / "revue.md").exists(), (
        "installer must NOT write a command-file shim — the skill provides /revue"
    )
    assert not (home / ".claude" / "commands" / "revue-local.md").exists(), (
        "installer must NOT write a /revue-local command-file shim"
    )


def test_install_aborts_when_claude_code_not_detected(tmp_path):
    # Arrange — HOME with no ~/.claude directory, uv on PATH
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "REVUE_INSTALL_NONINTERACTIVE": "1",
    }

    # Act
    result = _run_installer(env=env, cwd=tmp_path)

    # Assert — installer exits non-zero with an actionable message and installs
    # nothing (no skill written when Claude Code is absent).
    assert result.returncode != 0
    assert "Claude Code" in (result.stdout + result.stderr)
    assert not (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()


def test_install_is_idempotent_when_run_twice(installer_env):
    # Arrange
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act — run installer twice
    first = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])
    second = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — both succeed and uv tool install was called twice (idempotent upgrade-in-place)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    uv_log = (bin_dir / "uv.log").read_text().strip().splitlines()
    assert len(uv_log) == 2
    assert all("tool install" in line and "--force" in line for line in uv_log)
    skill_file = home / ".claude" / "skills" / "revue" / "SKILL.md"
    assert skill_file.exists(), "skill must still be present after re-run"
    assert not (home / ".claude" / "commands" / "revue.md").exists(), (
        "no command-file shim is ever written, even on re-run"
    )


def test_install_removes_stale_command_shims_global_scope(installer_env):
    """Upgrade cleanup: a prior installer's stale command-file shims (revue.md
    from the duplicate-shim era, revue-local.md from the original era) are
    removed so the upgraded install doesn't keep a dangling /revue-local or a
    duplicate /revue colliding with the skill."""
    # Arrange — global scope with both stale shims pre-seeded under ~/.claude.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    commands_dir = home / ".claude" / "commands"
    commands_dir.mkdir(parents=True)
    stale_revue = commands_dir / "revue.md"
    stale_local = commands_dir / "revue-local.md"
    stale_revue.write_text("# stale duplicate shim\n")
    stale_local.write_text("# stale legacy shim\n")
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — both stale shims removed; skill installed as the sole /revue source.
    assert result.returncode == 0, result.stderr
    assert not stale_revue.exists(), "stale revue.md shim must be removed on upgrade"
    assert not stale_local.exists(), "stale revue-local.md shim must be removed on upgrade"
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()


def test_install_removes_stale_command_shims_project_scope(installer_env):
    """Upgrade cleanup works in project scope too — stale shims under
    <project>/.claude/commands are removed."""
    # Arrange — project scope with both stale shims pre-seeded under the project.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    project = workspace / "myrepo"
    project.mkdir()
    commands_dir = project / ".claude" / "commands"
    commands_dir.mkdir(parents=True)
    stale_revue = commands_dir / "revue.md"
    stale_local = commands_dir / "revue-local.md"
    stale_revue.write_text("# stale duplicate shim\n")
    stale_local.write_text("# stale legacy shim\n")
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = str(project)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — both stale shims removed under the project; skill installed there.
    assert result.returncode == 0, result.stderr
    assert not stale_revue.exists(), "stale project revue.md shim must be removed"
    assert not stale_local.exists(), "stale project revue-local.md shim must be removed"
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()


def test_install_reuses_existing_revue_yml_in_workspace(installer_env):
    # Arrange — workspace contains a .revue.yml with marker content
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    existing_yml = workspace / ".revue.yml"
    marker = "review:\n  model: deepseek/deepseek-v4-pro  # user-customised\n"
    existing_yml.write_text(marker)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=workspace)

    # Assert — file untouched and installer surfaces that it was reused
    assert result.returncode == 0, result.stderr
    assert existing_yml.read_text() == marker, ".revue.yml content must not be modified"
    combined = result.stdout + result.stderr
    assert ".revue.yml" in combined and "reusing" in combined.lower()


def test_install_writes_default_revue_yml_when_missing(installer_env):
    # Arrange — workspace has NO .revue.yml
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    target_yml = workspace / ".revue.yml"
    assert not target_yml.exists()
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=workspace)

    # Assert — default .revue.yml created with schema version + provider keys
    assert result.returncode == 0, result.stderr
    assert target_yml.exists(), "installer must create a default .revue.yml when missing"
    body = target_yml.read_text()
    assert 'version: "1"' in body, "default config must declare schema version 1"
    assert "provider: openrouter" in body, "default config must specify openrouter provider"
    assert "deepseek/deepseek-v4-pro" in body, "default config must use the production model"
    assert "REVUE_API_KEY" in body, "default config must use vendor-agnostic env var name"
    combined = result.stdout + result.stderr
    assert "default" in combined.lower() and ".revue.yml" in combined


def test_install_does_not_overwrite_existing_revue_yml_on_rerun(installer_env):
    # Arrange — workspace has a user-customised .revue.yml
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    existing_yml = workspace / ".revue.yml"
    user_config = 'version: "1"\nai:\n  model: anthropic/claude-sonnet-4-6\n'
    existing_yml.write_text(user_config)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act — run installer twice (idempotency must preserve user config)
    _run_installer(env=installer_env["env"], cwd=workspace)
    second = _run_installer(env=installer_env["env"], cwd=workspace)

    # Assert — user config preserved verbatim across re-runs
    assert second.returncode == 0, second.stderr
    assert existing_yml.read_text() == user_config, (
        "user-customised .revue.yml must never be overwritten by re-running the installer"
    )


def test_install_skipped_when_neither_uv_nor_pipx_available(installer_env):
    # Arrange — empty PATH bin, no uv, no pipx
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    # Deliberately no _make_stub for uv or pipx
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — installer fails with a clear instruction to install uv or pipx
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "uv" in combined and "pipx" in combined


# ---------------------------------------------------------------------------
# REVUE-354 — interactive install wizard: scope (global vs project) + path.
#
# These tests drive the resolved *branch* of the wizard via env vars / CLI
# flags (deterministic, no pty). The interactive prompt *wording* (AC1/AC2/AC10
# text) is not asserted here — that requires a pty harness and is deferred:
#   # TODO REVUE-354 pty test — assert prompt strings via a pty/expect harness.
# ---------------------------------------------------------------------------


def test_wizard_dash_y_flag_forces_global_default(installer_env):
    """AC8: ``--yes`` forces global scope and skips all prompts."""
    # Arrange — drop the legacy non-interactive var so only --yes drives scope.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    env = dict(installer_env["env"])
    env.pop("REVUE_INSTALL_NONINTERACTIVE", None)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(
        env=env, cwd=installer_env["workspace"], args=["--yes"]
    )

    # Assert — global skill written under ~/.claude, never the workspace.
    assert result.returncode == 0, result.stderr
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    workspace: Path = installer_env["workspace"]
    assert not (workspace / ".claude").exists(), "global install must not write project .claude/"


def test_wizard_env_vars_skip_prompt_global(installer_env):
    """AC6: ``REVUE_INSTALL_SCOPE=global`` resolves to global non-interactively."""
    # Arrange
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    env = dict(installer_env["env"])
    env.pop("REVUE_INSTALL_NONINTERACTIVE", None)
    env["REVUE_INSTALL_SCOPE"] = "global"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=installer_env["workspace"])

    # Assert — skill lands under ~/.claude.
    assert result.returncode == 0, result.stderr
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()


def test_wizard_env_vars_skip_prompt_project(installer_env):
    """AC3/AC4/AC5/AC6: ``REVUE_INSTALL_SCOPE=project`` writes everything under <project>."""
    # Arrange — project dir is distinct from cwd (workspace) so AC5 discriminates.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    project = workspace / "myrepo"
    project.mkdir()
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"  # outranks legacy NONINTERACTIVE
    env["REVUE_INSTALL_PATH"] = str(project)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — AC4 skill, AC5 .revue.yml all under <project>.
    assert result.returncode == 0, result.stderr
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists(), "AC4"
    assert (project / ".revue.yml").exists(), "AC5"
    # No command-file shim is written in any scope.
    assert not (project / ".claude" / "commands" / "revue.md").exists()
    # Global skill location must NOT be written for a project install.
    assert not (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    # AC5: .revue.yml goes in the project, never in cwd.
    assert not (workspace / ".revue.yml").exists()


def test_wizard_project_scope_passes_target_dir_to_revue_install_skill(installer_env):
    """AC4: project install passes ``--target-dir <project>/.claude/skills``."""
    # Arrange
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    project = workspace / "myrepo"
    project.mkdir()
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = str(project)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — the revue stub log records the project-scoped --target-dir.
    assert result.returncode == 0, result.stderr
    revue_log = (bin_dir / "revue.log").read_text()
    expected = f"--target-dir {project}/.claude/skills"
    assert expected in revue_log, f"expected {expected!r} in revue.log, got:\n{revue_log}"


def test_wizard_env_path_supports_tilde_expansion(installer_env):
    """AC2: ``REVUE_INSTALL_PATH=~/x`` expands to ``$HOME/x``."""
    # Arrange — project under HOME, referenced via a literal leading tilde.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    project = home / "tildeproj"
    project.mkdir()
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = "~/tildeproj"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=installer_env["workspace"])

    # Assert — files land under the expanded $HOME/tildeproj path.
    assert result.returncode == 0, result.stderr
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    assert (project / ".revue.yml").exists()


def test_wizard_falls_back_to_global_when_no_tty(installer_env):
    """AC7: no /dev/tty → global fallback, one-line message, exit 0."""
    # Arrange — strip every resolution var so the installer reaches the tty
    # probe; detach the controlling terminal so /dev/tty cannot be opened.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    env = dict(installer_env["env"])
    for key in (
        "REVUE_INSTALL_NONINTERACTIVE",
        "REVUE_INSTALL_SCOPE",
        "REVUE_INSTALL_PATH",
    ):
        env.pop(key, None)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act — detach_tty makes /dev/tty unopenable in the child.
    result = _run_installer(
        env=env, cwd=installer_env["workspace"], detach_tty=True
    )

    # Assert — exit 0, global install performed, fallback message surfaced.
    assert result.returncode == 0, result.stderr
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    combined = (result.stdout + result.stderr).lower()
    assert "global" in combined and "tty" in combined


def test_wizard_aborts_on_missing_project_path(installer_env):
    """AC9: non-interactive project install with a missing path → hard error."""
    # Arrange — point at a project dir that does not exist.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    missing = workspace / "does-not-exist"
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = str(missing)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — non-zero exit with an actionable message naming the path.
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert str(missing) in combined
    assert not (missing / ".claude").exists()


def test_yes_flag_completes_quick_update_when_global_install_exists(installer_env):
    """``--yes`` + an existing global install completes the FULL install flow.

    NOTE (finding D, test honesty): this asserts only the *non-interactive*
    --yes path — it does NOT exercise the interactive AC10 "[Q]uick update /
    [M]odify scope?" prompt. ``--yes`` hits the first precedence rule and never
    enters resolve_scope's interactive existing-install branch, so the detection
    block could be deleted and this test would still pass. The interactive Q/M
    prompt + detection is pty-deferred:
    # TODO REVUE-354 pty test — assert the interactive [Q]/[M] prompt + that
    #   Quick reuses the existing global scope.
    What this DOES guarantee: --yes with a stale install still runs the package
    install with --force and refreshes the skill with --overwrite (the quick
    update IS the normal flow, not a short-circuit).
    """
    # Arrange — pre-seed an existing (stale) global skill install.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    skills_dir = home / ".claude" / "skills"
    (skills_dir / "revue").mkdir(parents=True)
    (skills_dir / "revue" / "SKILL.md").write_text("# stale skill\n")
    env = dict(installer_env["env"])
    env.pop("REVUE_INSTALL_NONINTERACTIVE", None)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=skills_dir)

    # Act — --yes must NOT hang; it completes the full flow non-interactively.
    result = _run_installer(
        env=env, cwd=installer_env["workspace"], args=["--yes"]
    )

    # Assert — completes, re-runs the package install (--force), uses --overwrite.
    assert result.returncode == 0, result.stderr
    uv_log = (bin_dir / "uv.log").read_text()
    assert "tool install" in uv_log and "--force" in uv_log
    revue_log = (bin_dir / "revue.log").read_text()
    assert "--overwrite" in revue_log, "quick-update must overwrite the stale skill"


def test_wizard_global_revue_yml_stays_in_cwd(installer_env):
    """Regression: a global install still writes .revue.yml into $(pwd)."""
    # Arrange — explicit global scope, cwd == workspace.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    env = dict(installer_env["env"])
    env.pop("REVUE_INSTALL_NONINTERACTIVE", None)
    env["REVUE_INSTALL_SCOPE"] = "global"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — .revue.yml created in cwd (workspace), not under ~/.claude.
    assert result.returncode == 0, result.stderr
    assert (workspace / ".revue.yml").exists()


# ---------------------------------------------------------------------------
# REVUE-354 — code-review rework (findings #3, #4, #5, #7).
#
# Findings #1, #2, #6 are interactive-only (existing-install ordering, project
# existing-install notice, prompts on the controlling terminal). They have no
# deterministic harness here and are exercised manually; see the implementation
# and the pty TODO below. The four findings tested here are env/flag-driven.
#   # TODO REVUE-354 pty test — cover #1/#2/#6 with a pty/expect harness.
# ---------------------------------------------------------------------------


def test_install_path_ignored_warning_when_scope_global(installer_env):
    """Finding #3: REVUE_INSTALL_PATH set + global scope → one-line WARN, not dropped silently."""
    # Arrange — explicit global scope but a project path is also provided.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    env = dict(installer_env["env"])
    env.pop("REVUE_INSTALL_NONINTERACTIVE", None)
    env["REVUE_INSTALL_SCOPE"] = "global"
    env["REVUE_INSTALL_PATH"] = str(workspace / "unused-project")
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — install is global AND the warning is surfaced (not a hard error).
    assert result.returncode == 0, result.stderr
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    combined = result.stdout + result.stderr
    assert "REVUE_INSTALL_PATH ignored" in combined
    # The unused project path must NOT have received an install.
    assert not (workspace / "unused-project" / ".claude").exists()


def test_project_scope_succeeds_without_claude_home_dir(tmp_path):
    """Finding #4: project scope works on a fresh machine with no ~/.claude, given the claude host CLI."""
    # Arrange — HOME has NO ~/.claude directory; the `claude` host CLI IS on PATH.
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    project = tmp_path / "project"
    home.mkdir()
    bin_dir.mkdir()
    project.mkdir()
    assert not (home / ".claude").exists(), "precondition: no ~/.claude on this fresh machine"
    _make_stub(bin_dir, "claude")  # host CLI present → detection should pass
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "REVUE_INSTALL_SCOPE": "project",
        "REVUE_INSTALL_PATH": str(project),
    }

    # Act
    result = _run_installer(env=env, cwd=tmp_path)

    # Assert — succeeds and writes under the project, despite ~/.claude absence.
    assert result.returncode == 0, result.stderr
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    assert not (project / ".claude" / "commands" / "revue.md").exists()
    assert (project / ".revue.yml").exists()
    assert not (home / ".claude").exists(), "project install must not create ~/.claude"


def test_install_aborts_when_claude_host_cli_missing(tmp_path):
    """Finding #4: with no `claude` host CLI on PATH, detection fails (even if ~/.claude exists)."""
    # Arrange — ~/.claude dir present, but the `claude` binary is NOT on PATH.
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    (home / ".claude").mkdir()  # stale dir must NOT be treated as detection signal
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "REVUE_INSTALL_NONINTERACTIVE": "1",
    }

    # Act
    result = _run_installer(env=env, cwd=tmp_path)

    # Assert — aborts with the Claude Code message; no package install attempted.
    assert result.returncode != 0
    assert "Claude Code" in (result.stdout + result.stderr)
    assert not (bin_dir / "uv.log").exists(), "must abort before the package install"


def test_install_tilde_user_path_rejected_when_unresolvable(installer_env):
    """Finding #5: a ~user path that cannot be resolved → actionable error, no eval."""
    # Arrange — a bogus username that getent/dscl cannot resolve.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = "~revue_nonexistent_user_xyz/sub"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=installer_env["workspace"])

    # Assert — non-zero exit with a clear message; package install never runs.
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "revue_nonexistent_user_xyz" in combined
    assert "resolve home directory" in combined
    assert not (bin_dir / "uv.log").exists(), "must reject before the package install"


def test_install_tilde_slash_path_expands_to_home(installer_env):
    """Finding #5 (safe form): ``~/sub`` expands to ``$HOME/sub`` (no eval, no ~user lookup)."""
    # Arrange — bare ~/ form pointing at an existing dir under HOME.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    project = home / "sub"
    project.mkdir()
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = "~/sub"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=installer_env["workspace"])

    # Assert — files land under $HOME/sub.
    assert result.returncode == 0, result.stderr
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    assert (project / ".revue.yml").exists()


def test_install_fails_fast_before_package_install_on_unwritable_target(installer_env):
    """Finding #7: a target dir that cannot be created aborts BEFORE the package install."""
    # Arrange — project path exists, but a FILE sits where <project>/.claude must
    # be a directory, so `mkdir -p <project>/.claude/skills` fails.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    project = workspace / "proj"
    project.mkdir()
    (project / ".claude").write_text("not a directory\n")  # blocks mkdir -p
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "project"
    env["REVUE_INSTALL_PATH"] = str(project)
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — non-zero AND the package install never ran (no partial state).
    assert result.returncode != 0
    assert not (bin_dir / "uv.log").exists(), (
        "target-dir creation must fail BEFORE the uv package install (no partial install)"
    )
    # The skill was never installed either.
    assert not (project / ".claude" / "skills" / "revue").exists()


# ---------------------------------------------------------------------------
# REVUE-354 — 2nd review pass (findings A, B, C, D, E).
# Finding D is a rename (above). Finding E (fd-3 EXIT trap) is correct by
# construction but is NOT covered deterministically here: the only abort paths
# the suite can trigger reach error() with the tty never opened (SCOPE=project
# wins precedence before open_tty; the no-tty tests fail to open fd 3 by design),
# so none exercise "error after open_tty" — the exact leak the trap fixes.
#   # TODO REVUE-354 pty test — assert fd 3 is closed when error() fires after
#   #   an interactive open_tty.
# A/B/C add deterministic cases below.
# ---------------------------------------------------------------------------


def test_install_creates_no_dirs_when_no_package_manager(installer_env):
    """Finding A.1: with neither uv nor pipx, the installer creates NO .claude dirs."""
    # Arrange — no uv/pipx stubs; only the claude host CLI (from the fixture).
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "global"
    # Deliberately do NOT stub uv or pipx.

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — fails for lack of a package manager BEFORE creating any target dir.
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "uv" in combined and "pipx" in combined
    assert not (home / ".claude" / "commands").exists(), (
        "must not create commands dir when no package manager is available"
    )
    assert not (home / ".claude" / "skills").exists(), (
        "must not create skills dir when no package manager is available"
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses 0500 dir write protection")
def test_install_fails_fast_when_revue_yml_dir_unwritable(installer_env, tmp_path):
    """Finding A.2: an unwritable revue_yml_dir (global $(pwd)) fails BEFORE package install."""
    # Arrange — global scope; cwd is an existing dir made non-writable, so the
    # .revue.yml write would fail. The preflight must catch this up front.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "global"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    unwritable_cwd = tmp_path / "ro_cwd"
    unwritable_cwd.mkdir()
    os.chmod(unwritable_cwd, 0o500)  # r-x: file creation denied for non-root
    try:
        # Act — cwd is the unwritable dir, which is the global revue_yml_dir.
        result = _run_installer(env=env, cwd=unwritable_cwd)

        # Assert — fails fast: no package install ran, no skill written.
        assert result.returncode != 0
        assert not (bin_dir / "uv.log").exists(), (
            "unwritable .revue.yml dir must fail BEFORE the uv package install"
        )
        assert not (home / ".claude" / "skills" / "revue").exists()
        combined = result.stdout + result.stderr
        assert "not writable" in combined.lower()
    finally:
        os.chmod(unwritable_cwd, 0o700)  # restore so pytest can clean up


def test_install_honours_claude_config_dir_for_global_scope(installer_env, tmp_path):
    """Finding B: CLAUDE_CONFIG_DIR relocates the global config dir."""
    # Arrange — global scope with CLAUDE_CONFIG_DIR pointing at a custom dir.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    cfg = tmp_path / "cfg"
    env = dict(installer_env["env"])
    env["REVUE_INSTALL_SCOPE"] = "global"
    env["CLAUDE_CONFIG_DIR"] = str(cfg)
    _make_stub(bin_dir, "uv")
    # Skill stub falls back here only if --target-dir is absent; the installer
    # passes --target-dir <cfg>/skills, which the stub honours.
    _make_revue_stub(bin_dir, claude_skills_dir=cfg / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — skill lands under CLAUDE_CONFIG_DIR, not ~/.claude.
    assert result.returncode == 0, result.stderr
    assert (cfg / "skills" / "revue" / "SKILL.md").exists()
    assert not (home / ".claude" / "skills" / "revue" / "SKILL.md").exists(), (
        "must not write to ~/.claude when CLAUDE_CONFIG_DIR is set"
    )


def test_yes_flag_warns_scope_project_override(installer_env):
    """Finding C: ``--yes`` overriding REVUE_INSTALL_SCOPE=project emits a named warning."""
    # Arrange — --yes forces global, but the user also asked for project scope.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    env = dict(installer_env["env"])
    env.pop("REVUE_INSTALL_NONINTERACTIVE", None)
    env["REVUE_INSTALL_SCOPE"] = "project"
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace, args=["--yes"])

    # Assert — install is global AND a cause-named warning is surfaced.
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "--yes forces global scope" in combined
    assert "REVUE_INSTALL_SCOPE=project ignored" in combined


def test_noninteractive_warns_path_override(installer_env):
    """Finding C: REVUE_INSTALL_NONINTERACTIVE=1 overriding REVUE_INSTALL_PATH names the cause."""
    # Arrange — NONINTERACTIVE forces global; user also set a project PATH.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    env = dict(installer_env["env"])  # fixture already sets NONINTERACTIVE=1
    env["REVUE_INSTALL_PATH"] = str(workspace / "ignored-proj")
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=env, cwd=workspace)

    # Assert — global install, cause-named warning, PATH substring preserved.
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "REVUE_INSTALL_NONINTERACTIVE=1 forces global scope" in combined
    assert "REVUE_INSTALL_PATH ignored" in combined
    assert not (workspace / "ignored-proj" / ".claude").exists()


# --- REVUE-395: edge-case hardening (AC1 HOME guard, AC2 dscl spaces, AC3 no-path) ---


def test_tilde_path_with_unset_home_aborts(installer_env):
    """AC1: a ``~/`` path with HOME unset must abort, not silently expand to ``/``."""
    # Arrange — project scope, a ~/ path, and an EMPTY HOME.
    bin_dir: Path = installer_env["bin"]
    _make_stub(bin_dir, "uv")
    env = {
        "PATH": str(installer_env["env"]["PATH"]),
        "HOME": "",  # unset/empty
        "REVUE_INSTALL_SCOPE": "project",
        "REVUE_INSTALL_PATH": "~/proj",
    }

    # Act — no tty so the path comes straight from the env var.
    result = _run_installer(env=env, cwd=installer_env["workspace"], detach_tty=True)

    # Assert — aborts with an actionable message, never expands ~ to a root path.
    assert result.returncode != 0, "must abort when HOME is unset"
    assert "HOME is unset" in (result.stdout + result.stderr)


def test_tilde_path_with_truly_unset_home_aborts(installer_env):
    """AC1: a ``~/`` path with HOME *entirely absent* (not just empty) must abort
    with the actionable "HOME is unset" message — never crash on ``set -u`` with a
    raw "HOME: unbound variable" before ``expand_tilde``'s guard runs (the Docker
    e2e caught this; the empty-string sibling test could not).
    """
    # Arrange — project scope + a ~/ path, with HOME OMITTED from the env entirely.
    bin_dir: Path = installer_env["bin"]
    _make_stub(bin_dir, "uv")
    env = {
        "PATH": str(installer_env["env"]["PATH"]),
        # no HOME key at all → truly unset in the child process
        "REVUE_INSTALL_SCOPE": "project",
        "REVUE_INSTALL_PATH": "~/proj",
    }

    # Act — no tty so the path comes straight from the env var.
    result = _run_installer(env=env, cwd=installer_env["workspace"], detach_tty=True)

    # Assert — actionable abort, NOT a raw "unbound variable" crash.
    combined = result.stdout + result.stderr
    assert result.returncode != 0, "must abort when HOME is unset"
    assert "HOME is unset" in combined, combined
    assert "unbound variable" not in combined, combined


def test_global_scope_without_home_or_config_dir_errors(installer_env):
    """A GLOBAL install with neither HOME nor CLAUDE_CONFIG_DIR must error
    actionably about the config dir — never crash on ``set -u`` and never silently
    default to the root-relative ``/.claude``.
    """
    # Arrange — no scope/path env vars and no tty → AC7 falls back to global;
    # HOME omitted and CLAUDE_CONFIG_DIR unset → CLAUDE_HOME is unresolvable.
    bin_dir: Path = installer_env["bin"]
    _make_stub(bin_dir, "uv")
    env = {
        "PATH": str(installer_env["env"]["PATH"]),
        # no HOME, no CLAUDE_CONFIG_DIR, no REVUE_INSTALL_* → global fallback
    }

    # Act
    result = _run_installer(
        env=env, cwd=installer_env["workspace"], detach_tty=True
    )

    # Assert — actionable error naming the cause; no crash, no /.claude default.
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "CLAUDE_CONFIG_DIR" in combined or "global config directory" in combined, combined
    assert "unbound variable" not in combined, combined
    assert not Path("/.claude").exists(), "must not default to root-relative /.claude"


def test_project_scope_without_path_or_tty_warns_and_uses_cwd(installer_env):
    """AC3: project scope + no path + no tty falls back to cwd but WARNS (not silent)."""
    # Arrange — project scope, no REVUE_INSTALL_PATH, no tty.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    workspace: Path = installer_env["workspace"]
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")
    env = {
        "PATH": str(installer_env["env"]["PATH"]),
        "HOME": str(home),
        "REVUE_INSTALL_SCOPE": "project",
    }

    # Act
    result = _run_installer(env=env, cwd=workspace, detach_tty=True)

    # Assert — installs into cwd (project scope) AND warns about the implicit choice.
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "no REVUE_INSTALL_PATH set" in combined and "current directory" in combined
    assert (workspace / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    assert not (home / ".claude" / "skills").exists(), "project scope must not write global"


@pytest.mark.skipif(
    shutil.which("getent") is not None,
    reason="the dscl fallback is only exercised where getent is absent (macOS)",
)
def test_dscl_home_with_spaces_is_not_truncated(installer_env, tmp_path):
    """AC2: a macOS ``dscl`` home directory containing spaces must not be truncated."""
    # Arrange — a ~user path whose home (via a stub dscl) contains a space.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    spaced_home = tmp_path / "Users" / "john doe"
    spaced_home.mkdir(parents=True)
    dscl = bin_dir / "dscl"
    dscl.write_text(f'#!/bin/sh\necho "NFSHomeDirectory: {spaced_home}"\n')
    dscl.chmod(0o755)
    _make_stub(bin_dir, "uv")
    env = {
        "PATH": str(installer_env["env"]["PATH"]),
        "HOME": str(home),
        "REVUE_INSTALL_SCOPE": "project",
        "REVUE_INSTALL_PATH": "~john/sub",  # ~user → dscl resolution path
    }

    # Act — the resolved "<spaced_home>/sub" doesn't exist + no tty → AC9 error.
    result = _run_installer(env=env, cwd=installer_env["workspace"], detach_tty=True)

    # Assert — the error names the FULL spaced path (proves no awk truncation).
    combined = result.stdout + result.stderr
    assert str(spaced_home) in combined, f"spaced home was truncated; output={combined}"


# ---------------------------------------------------------------------------
# REVUE-360 — supported-platform guard (AC1 / TC1 / TC2)
# ---------------------------------------------------------------------------

# The canonical install-page URL the guard must link. Mirrors
# revue_core.platform_support.INSTALL_PAGE_URL (pinned by the consistency test).
_INSTALL_PAGE_URL = "https://github.com/Revue-sh/revue/blob/main/docs/guides/install.md"


def test_guard_blocks_linux_arm64_with_actionable_message(installer_env):
    # Arrange — simulate an unsupported Linux ARM (Graviton) box.
    bin_dir: Path = installer_env["bin"]
    _make_uname_stub(bin_dir, system="Linux", machine="aarch64")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — non-zero exit, and the message carries all three AC1 parts.
    assert result.returncode != 0, "guard must fail non-zero on an unsupported platform"
    assert "Linux aarch64" in result.stderr, "message must name the detected platform"
    assert _INSTALL_PAGE_URL in result.stderr, "message must link the install page"
    assert "revue-ci" in result.stderr, "message must state the /revue-CI workaround"


def test_guard_blocks_intel_mac_naming_the_platform(installer_env):
    # Arrange — Intel Macs report x86_64 under Darwin; explicitly unsupported.
    bin_dir: Path = installer_env["bin"]
    _make_uname_stub(bin_dir, system="Darwin", machine="x86_64")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert
    assert result.returncode != 0
    assert "Darwin x86_64" in result.stderr


def test_guard_runs_before_pip_so_no_package_manager_is_invoked(installer_env):
    # Arrange — unsupported platform with uv available; guard must short-circuit
    # BEFORE any install, so uv is never called and no partial state is created.
    bin_dir: Path = installer_env["bin"]
    _make_uname_stub(bin_dir, system="Windows", machine="amd64")
    _make_stub(bin_dir, "uv")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — failed, and uv was never invoked (no uv.log written).
    assert result.returncode != 0
    assert not (bin_dir / "uv.log").exists(), "pip/uv must not run on an unsupported platform"


def test_guard_allows_supported_linux_x86_64_and_install_proceeds(installer_env):
    # Arrange — simulate a supported Linux x86_64 box; full install should run.
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_uname_stub(bin_dir, system="Linux", machine="x86_64")
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — guard passed; uv install ran to completion.
    assert result.returncode == 0, result.stderr
    assert (bin_dir / "uv.log").exists(), "supported platform must reach the uv install step"


def test_guard_normalises_amd64_alias_to_x86_64_for_linux(installer_env):
    # Arrange — some toolchains report amd64; it is an alias for x86_64 (supported).
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_uname_stub(bin_dir, system="Linux", machine="amd64")
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — amd64 treated as x86_64, so the install proceeds.
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# REVUE-373 — install.sh version-check step must not abort under set -e
# ---------------------------------------------------------------------------


def _make_revue_stub_strict_version(bin_dir: Path, *, claude_skills_dir: Path) -> Path:
    """A revue stub that mimics the real CLI's subcommand behaviour for version:

    - ``revue version``     → exits 0  (the `version` subparser, always worked)
    - ``revue --version``   → exits 2  (would fail pre-REVUE-360; mimics old argparse rejection)
    - ``revue install-skill ...`` → exits 0 and writes the skill files

    This lets us assert that install.sh no longer calls ``revue --version`` and
    that the post-install step completes without aborting the installer.
    """
    log = bin_dir / "revue.log"
    script = bin_dir / "revue"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "revue $*" >> "{log}"
            if [ "$1" = "install-skill" ]; then
                target="{claude_skills_dir}"
                prev=""
                for arg in "$@"; do
                    if [ "$prev" = "--target-dir" ]; then
                        target="$arg"
                    fi
                    prev="$arg"
                done
                mkdir -p "$target/revue"
                printf '# revue skill\\n' > "$target/revue/SKILL.md"
                exit 0
            fi
            if [ "$1" = "version" ]; then
                echo "0.0.0-test"
                exit 0
            fi
            # Simulate old argparse rejection of --version (REVUE-373 regression guard).
            if [ "$1" = "--version" ]; then
                echo "error: argument command: invalid choice: --version" >&2
                exit 2
            fi
            exit 0
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return log


def test_install_version_step_uses_version_subcommand_not_double_dash(installer_env):
    """AC1 (static) — install.sh must call ``revue version``, not ``revue --version``.

    If someone edits install.sh and re-introduces ``revue --version``, this test
    catches it without needing to run the installer at all.
    """
    # Arrange / Act — read the script text directly.
    text = INSTALL_SCRIPT.read_text()

    # Assert — double-dash form is gone; subcommand form is present.
    assert "revue --version" not in text, (
        "REVUE-373: install.sh must use 'revue version', not 'revue --version' "
        "(--version was rejected by argparse on old wheels)"
    )
    assert "revue version" in text, (
        "REVUE-373: install.sh must call 'revue version' to verify the install"
    )


def test_install_version_step_does_not_abort_when_version_subcommand_fails(installer_env):
    """AC2 — a failing version check must NOT abort a successful install.

    Uses a stub that correctly exits 0 on ``revue version`` but exits 2 on
    ``revue --version`` (old argparse behaviour).  If install.sh were still
    calling ``--version``, the stub would abort the script under set -e and
    the test would fail.
    """
    # Arrange
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_stub(bin_dir, "uv")
    revue_log = _make_revue_stub_strict_version(
        bin_dir, claude_skills_dir=home / ".claude" / "skills"
    )

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — installer exits 0 and the version subcommand was called (not --version).
    assert result.returncode == 0, result.stderr
    log_text = revue_log.read_text()
    assert "revue version" in log_text, (
        f"expected 'revue version' in revue.log, got:\n{log_text}"
    )
    assert "revue --version" not in log_text, (
        f"installer must not call 'revue --version'; got:\n{log_text}"
    )

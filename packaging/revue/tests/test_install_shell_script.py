"""Tests for ``scripts/install.sh`` — the one-command curl-pipe-bash installer.

The installer (REVUE-276 / E-P2A-S2) lives at the repo root under
``scripts/install.sh`` and is fetched by end users via:

    curl -fsSL https://raw.githubusercontent.com/cbscd/revue/main/scripts/install.sh | bash

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
    """Stub the ``revue`` CLI so it simulates ``install-skill`` writing files."""
    log = bin_dir / "revue.log"
    script = bin_dir / "revue"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "revue $*" >> "{log}"
            if [ "$1" = "install-skill" ]; then
                mkdir -p "{claude_skills_dir}/revue"
                printf '# revue skill\\n' > "{claude_skills_dir}/revue/SKILL.md"
            fi
            exit 0
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return log


def _run_installer(*, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — controlled script, controlled env
        ["bash", str(INSTALL_SCRIPT)],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
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


def test_install_writes_claude_code_slash_command(installer_env):
    # Arrange — Claude Code present, uv available, revue stubbed
    bin_dir: Path = installer_env["bin"]
    home: Path = installer_env["home"]
    _make_stub(bin_dir, "uv")
    _make_revue_stub(bin_dir, claude_skills_dir=home / ".claude" / "skills")

    # Act
    result = _run_installer(env=installer_env["env"], cwd=installer_env["workspace"])

    # Assert — slash command descriptor written to ~/.claude/commands/revue-local.md
    assert result.returncode == 0, result.stderr
    command_file = home / ".claude" / "commands" / "revue-local.md"
    assert command_file.exists(), f"missing slash command file: {command_file}"
    body = command_file.read_text()
    assert "/revue-local" in body, "slash command body must reference /revue-local"
    assert "revue install-skill" not in body, (
        "slash command body is the user-facing prompt, not setup instructions"
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

    # Assert — installer exits non-zero with an actionable message
    assert result.returncode != 0
    assert "Claude Code" in (result.stdout + result.stderr)
    assert not (home / ".claude" / "commands" / "revue-local.md").exists()


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
    command_file = home / ".claude" / "commands" / "revue-local.md"
    assert command_file.exists(), "slash command must still be present after re-run"


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

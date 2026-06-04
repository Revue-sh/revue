"""pty-driven tests for the *interactive* paths of ``scripts/install.sh`` (REVUE-395 AC4).

The non-interactive paths (env vars, ``--yes``) are covered by
``test_install_shell_script.py`` via ``subprocess``. The wizard's interactive
prompts, however, read from / write to the controlling terminal (``/dev/tty``
on fd 3) and can only be driven under a real pseudo-terminal — which is why the
original story left them behind a ``# TODO REVUE-354 pty test`` marker.

Each test spawns the installer under a pty, feeds scripted keystrokes (the
prompts are line-buffered, so the whole answer sequence can be written once and
is consumed one ``read`` at a time), and asserts the resulting scope/path
placement. All external commands (``claude``/``uv``/``pipx``/``revue``) are
stubbed, so only the wizard's branching logic runs — no real package install,
no network.
"""
from __future__ import annotations

import os
import pty
import select
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(
    not hasattr(pty, "fork") or sys.platform == "win32",
    reason="interactive-prompt tests require a POSIX pseudo-terminal",
)

# A `revue` stub whose `install-skill --target-dir T` creates `T/revue/SKILL.md`,
# so each test can assert the skill landed in the scope-appropriate directory.
_REVUE_STUB = (
    "#!/bin/sh\n"
    'if [ "$1" = "install-skill" ]; then\n'
    '  target=""; prev=""\n'
    '  for arg in "$@"; do [ "$prev" = "--target-dir" ] && target="$arg"; prev="$arg"; done\n'
    '  mkdir -p "$target/revue"; printf "# skill\\n" > "$target/revue/SKILL.md"\n'
    "fi\n"
)


def _stub(bin_dir: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path: Path):
    """Isolated HOME + stub bin on PATH. Returns (env, home, workdir)."""
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for cmd in ("claude", "uv", "pipx"):
        _stub(bin_dir, cmd)
    _stub(bin_dir, "revue", _REVUE_STUB)
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "TERM": "xterm"}
    return env, home, work


def _drive(answers: str, env: dict, cwd: Path) -> tuple[str, int]:
    """Run install.sh under a pty, feed ``answers``, return (combined_output, exit_code)."""
    pid, master_fd = pty.fork()
    if pid == 0:  # child: the pty is its controlling terminal, so /dev/tty == this pty
        os.chdir(str(cwd))
        os.environ.clear()
        os.environ.update(env)
        os.execvp("bash", ["bash", str(INSTALL_SCRIPT)])
    output = b""
    answered = False
    start = time.time()
    payload = answers.encode()
    while True:
        readable, _, _ = select.select([master_fd], [], [], 0.2)
        if readable:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:  # slave closed on child exit
                break
            if not chunk:
                break
            output += chunk
        if not answered and time.time() - start > 0.4:
            os.write(master_fd, payload)  # buffered; line-reads consume in order
            answered = True
        if time.time() - start > 15:  # safety net — a real prompt resolves in well under this
            break
    try:
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        code = -1
    return output.decode(errors="replace"), code


def _seed_global_install(home: Path) -> None:
    skill = home / ".claude" / "skills" / "revue"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# stale\n")


def _seed_project_install(project: Path) -> None:
    skill = project / ".claude" / "skills" / "revue"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# stale\n")


# --- scope prompt (AC1) ---

def test_enter_selects_global_default(sandbox):
    env, home, work = sandbox
    output, code = _drive("\n", env, cwd=work)
    assert code == 0, output
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    assert (work / ".revue.yml").exists()  # global keeps .revue.yml in cwd


def test_letter_g_selects_global(sandbox):
    env, home, work = sandbox
    output, code = _drive("G\n", env, cwd=work)
    assert code == 0, output
    assert (home / ".claude" / "commands" / "revue-local.md").exists()


# --- project scope + path prompt (AC2/3/4/5) ---

def test_p_then_blank_path_uses_cwd(sandbox):
    env, home, work = sandbox
    output, code = _drive("P\n\n", env, cwd=work)
    assert code == 0, output
    assert (work / ".claude" / "skills" / "revue" / "SKILL.md").exists()
    assert (work / ".revue.yml").exists()
    assert not (home / ".claude").exists()  # project scope never touches global


def test_p_then_explicit_path(sandbox, tmp_path):
    env, home, work = sandbox
    project = tmp_path / "explicit"
    project.mkdir()
    output, code = _drive(f"P\n{project}\n", env, cwd=work)
    assert code == 0, output
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()


# --- missing-path handling (AC9) ---

def test_missing_path_then_yes_creates_and_installs(sandbox, tmp_path):
    env, home, work = sandbox
    target = tmp_path / "make-me"
    output, code = _drive(f"P\n{target}\nY\n", env, cwd=work)
    assert code == 0, output
    assert (target / ".claude" / "skills" / "revue" / "SKILL.md").exists()


def test_missing_path_then_no_aborts_without_creating(sandbox, tmp_path):
    env, home, work = sandbox
    target = tmp_path / "do-not-make"
    output, code = _drive(f"P\n{target}\nn\n", env, cwd=work)
    assert code != 0, output
    assert not target.exists()


# --- existing-install detection + the AC10 ordering fix ---

def test_existing_global_enter_quick_updates_and_is_asked_first(sandbox):
    """The Quick/Modify prompt must appear BEFORE any scope prompt — the AC10
    ordering fix, so a Project choice can never be silently discarded."""
    env, home, work = sandbox
    _seed_global_install(home)
    output, code = _drive("\n", env, cwd=work)
    assert code == 0, output
    assert "uick update" in output  # "[Q]uick update (default)" shown up front
    assert (home / ".claude" / "skills" / "revue" / "SKILL.md").exists()


def test_existing_global_modify_then_project(sandbox, tmp_path):
    env, home, work = sandbox
    _seed_global_install(home)
    project = tmp_path / "modproj"
    project.mkdir()
    output, code = _drive(f"M\nP\n{project}\n", env, cwd=work)
    assert code == 0, output
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()


# --- the two sub-branches the prototype skipped (per review request) ---

def test_existing_global_modify_then_global(sandbox):
    """Modify-scope → choose Global again → completes as a global (re)install."""
    env, home, work = sandbox
    _seed_global_install(home)
    output, code = _drive("M\nG\n", env, cwd=work)
    assert code == 0, output
    assert (home / ".claude" / "commands" / "revue-local.md").exists()


def test_existing_project_install_detected_in_place(sandbox, tmp_path):
    """An existing PROJECT install is detected after the path resolves and
    refreshed in place — review finding #2 (it must not be invisible)."""
    env, home, work = sandbox
    project = tmp_path / "existingproj"
    project.mkdir()
    _seed_project_install(project)
    output, code = _drive(f"P\n{project}\n", env, cwd=work)
    assert code == 0, output
    assert "roject install detected" in output  # the in-place refresh notice
    assert (project / ".claude" / "skills" / "revue" / "SKILL.md").exists()

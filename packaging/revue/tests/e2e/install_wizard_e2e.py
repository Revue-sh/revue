#!/usr/bin/env python3
"""End-to-end harness for ``scripts/install.sh`` — runs INSIDE a disposable
container (see ``run_install_e2e.sh``).

Unlike the in-tree pytest suites (``test_install_shell_pty.py`` /
``test_install_shell_script.py``), which run against the host and stub the
controlling terminal, this harness drives the *real* wizard end-to-end in a
throwaway Linux container: a real pty for the interactive prompts, real
filesystem placement, and the no-tty fallbacks. External commands
(``claude``/``uv``/``pipx``/``revue``) are stubbed so only the wizard's branching
logic runs — offline, no real install, no network.

It is intentionally NOT collected by pytest (no ``test_`` prefix and it needs
Docker + a container filesystem it is free to mutate, e.g. ``/proj4``, ``/new6``).
Run it via ``run_install_e2e.sh`` from a clean checkout; CI may invoke that
runner as an optional gate.

The installer under test defaults to ``/tmp/install.sh`` (where the runner copies
it) and can be overridden with ``INSTALL_SH``. Exit code is non-zero if any check
fails, so the runner can gate on it.

Coverage note: AC2 (macOS ``dscl`` home-dir spaces) is NOT exercised here — a
Linux container has ``getent``, so the ``dscl`` branch never fires. AC2 stays
covered by the macOS-only ``test_dscl_home_with_spaces_is_not_truncated``
(skipped where ``getent`` is present).
"""
import os
import pty
import select
import shutil
import subprocess
import sys
import time

INSTALL = os.environ.get("INSTALL_SH", "/tmp/install.sh")
BIN = "/usr/local/bin"


def stub(name, body="#!/bin/sh\nexit 0\n"):
    path = f"{BIN}/{name}"
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


def setup_stubs():
    for cmd in ("claude", "uv", "pipx"):
        stub(cmd)
    # `revue install-skill --target-dir T` → create T/revue/SKILL.md so each
    # check can assert the skill landed in the scope-appropriate directory.
    stub(
        "revue",
        '#!/bin/sh\nif [ "$1" = install-skill ]; then t=; p=; for a in "$@"; do '
        '[ "$p" = --target-dir ] && t="$a"; p="$a"; done; mkdir -p "$t/revue"; '
        'echo skill > "$t/revue/SKILL.md"; fi\n',
    )


def run(answers, env, cwd):
    """Spawn install.sh under a pty (so /dev/tty == this pty), feed answers."""
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(env)
        os.execvp("bash", ["bash", INSTALL])
    out = b""
    sent = False
    start = time.time()
    payload = answers.encode()
    while True:
        readable, _, _ = select.select([fd], [], [], 0.2)
        if readable:
            try:
                chunk = os.read(fd, 4096)
            except OSError:  # slave closed on child exit
                break
            if not chunk:
                break
            out += chunk
        if not sent and time.time() - start > 0.4:
            os.write(fd, payload)  # line-buffered reads consume answers in order
            sent = True
        if time.time() - start > 12:  # safety net
            break
    try:
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        code = -1
    return out.decode(errors="replace"), code


def run_notty(env, cwd):
    """Run install.sh with NO controlling terminal (start_new_session + no stdin),
    so /dev/tty cannot open and the wizard takes its non-interactive branches."""
    proc = subprocess.run(
        ["bash", INSTALL],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        timeout=30,
    )
    return proc.stdout.decode(errors="replace"), proc.returncode


def fresh_home(tag):
    home = f"/homes/{tag}"
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home, exist_ok=True)
    return home


def env_for(home):
    return {"HOME": home, "PATH": f"{BIN}:/usr/bin:/bin", "TERM": "xterm"}


def has(path):
    return os.path.exists(path)


def seed_global_install(home):
    os.makedirs(f"{home}/.claude/skills/revue", exist_ok=True)
    with open(f"{home}/.claude/skills/revue/SKILL.md", "w") as fh:
        fh.write("# stale\n")


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, cond, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def last_line(out):
    stripped = out.strip()
    return stripped.splitlines()[-1] if stripped else ""


def main():
    if not os.path.exists(INSTALL):
        print(f"installer not found at {INSTALL} (set INSTALL_SH or use run_install_e2e.sh)")
        sys.exit(2)
    setup_stubs()
    os.makedirs("/homes", exist_ok=True)

    print("--- interactive (pty) paths ---")

    # 1. blank Enter → Global (default)
    h = fresh_home("s1"); os.makedirs("/w1", exist_ok=True)
    out, code = run("\n", env_for(h), "/w1")
    check("1 Enter→Global: exit 0", code == 0, f"exit={code}")
    check("1 Enter→Global: ~/.claude has skill (and no command-file shim)",
          has(f"{h}/.claude/skills/revue/SKILL.md") and not has(f"{h}/.claude/commands/revue.md"))
    check("1 Enter→Global: .revue.yml in cwd", has("/w1/.revue.yml"))

    # 2. "G" → Global
    h = fresh_home("s2"); os.makedirs("/w2", exist_ok=True)
    out, code = run("G\n", env_for(h), "/w2")
    check("2 'G'→Global: ~/.claude populated", code == 0 and has(f"{h}/.claude/skills/revue/SKILL.md"), f"exit={code}")

    # 3. "P" + blank path → Project = cwd
    h = fresh_home("s3"); os.makedirs("/w3", exist_ok=True)
    out, code = run("P\n\n", env_for(h), "/w3")
    check("3 'P'+blank→Project=cwd: project .claude + .revue.yml",
          code == 0 and has("/w3/.claude/skills/revue/SKILL.md") and has("/w3/.revue.yml"), f"exit={code}")
    check("3 'P': nothing written to ~/.claude", not has(f"{h}/.claude"))

    # 4. "P" + explicit existing path
    h = fresh_home("s4"); os.makedirs("/proj4", exist_ok=True)
    out, code = run("P\n/proj4\n", env_for(h), "/homes/s4")
    check("4 'P'+/proj4→Project there",
          code == 0 and has("/proj4/.claude/skills/revue/SKILL.md") and has("/proj4/.revue.yml"), f"exit={code}")

    # 5. "P" + ~/sub (tilde expands; dir missing → create [Y])
    h = fresh_home("s5")
    out, code = run("P\n~/sub5\nY\n", env_for(h), "/homes/s5")
    check("5 'P'+~/sub5+Y: tilde-expanded + created under HOME",
          code == 0 and has(f"{h}/sub5/.claude/skills/revue/SKILL.md"), f"exit={code}")

    # 6. "P" + missing path + "Y" → create + install
    h = fresh_home("s6")
    out, code = run("P\n/new6\nY\n", env_for(h), "/homes/s6")
    check("6 'P'+/new6+Y: created + installed", code == 0 and has("/new6/.claude/skills/revue/SKILL.md"), f"exit={code}")

    # 7. "P" + missing path + "n" → ABORT
    h = fresh_home("s7")
    out, code = run("P\n/nope7\nn\n", env_for(h), "/homes/s7")
    check("7 'P'+/nope7+n: aborts (exit!=0)", code != 0, f"exit={code}")
    check("7 abort: /nope7 NOT created", not has("/nope7"))

    # 8. Existing global install + blank → Quick-update (global)
    h = fresh_home("s8"); seed_global_install(h); os.makedirs("/w8", exist_ok=True)
    out, code = run("\n", env_for(h), "/w8")
    check("8 existing-global + Enter→Quick: exit 0, stays global",
          code == 0 and has(f"{h}/.claude/skills/revue/SKILL.md") and not has("/w8/.claude"), f"exit={code}")
    check("8 prompt shown was Quick/Modify", "Quick update" in out or "uick" in out)

    # 9. Existing global install + "M" → Modify → choose Project
    h = fresh_home("s9"); seed_global_install(h); os.makedirs("/proj9", exist_ok=True)
    out, code = run("M\nP\n/proj9\n", env_for(h), "/homes/s9")
    check("9 existing-global + M→scope→Project: lands in /proj9",
          code == 0 and has("/proj9/.claude/skills/revue/SKILL.md"), f"exit={code}")

    print("--- no-tty edge-case paths (REVUE-395 hardening) ---")

    # 10. unset HOME + project scope + ~/path → tilde guard errors, no /sub10
    env = {"PATH": f"{BIN}:/usr/bin:/bin", "TERM": "xterm",
           "REVUE_INSTALL_SCOPE": "project", "REVUE_INSTALL_PATH": "~/sub10"}  # HOME deliberately absent
    os.makedirs("/homes/s10", exist_ok=True)
    out, code = run_notty(env, "/homes/s10")
    check("10 unset-HOME + ~/sub10: aborts non-zero", code != 0, f"exit={code}")
    check("10 unset-HOME: error names 'HOME is unset'", "HOME is unset" in out, last_line(out))
    check("10 unset-HOME: did NOT silently install to /sub10", not has("/sub10"))

    # 11. project scope, NO path, no tty → warns + uses cwd (not silent)
    h = fresh_home("s11"); os.makedirs("/w11", exist_ok=True)
    env = {**env_for(h), "REVUE_INSTALL_SCOPE": "project"}  # no REVUE_INSTALL_PATH
    out, code = run_notty(env, "/w11")
    check("11 no-path project no-tty: exit 0", code == 0, f"exit={code}")
    check("11 no-path: WARNS, names cwd (not silent)",
          "no REVUE_INSTALL_PATH" in out and "/w11" in out, last_line(out))
    check("11 no-path: installed into cwd", has("/w11/.claude/skills/revue/SKILL.md"))

    # 12. nothing set, no tty → global fallback with notice
    h = fresh_home("s12"); os.makedirs("/w12", exist_ok=True)
    out, code = run_notty(env_for(h), "/w12")
    check("12 no-tty nothing-set: global fallback exit 0",
          code == 0 and has(f"{h}/.claude/skills/revue/SKILL.md"), f"exit={code}")
    check("12 no-tty: notice names /dev/tty fallback", "dev/tty" in out or "falling back to global" in out)

    # 13. global scope + neither HOME nor CLAUDE_CONFIG_DIR → actionable error, no /.claude
    env = {"PATH": f"{BIN}:/usr/bin:/bin", "TERM": "xterm"}  # no HOME, no CLAUDE_CONFIG_DIR
    os.makedirs("/w13", exist_ok=True)
    out, code = run_notty(env, "/w13")
    check("13 global + no HOME/CLAUDE_CONFIG_DIR: aborts non-zero", code != 0, f"exit={code}")
    check("13 global no-home: actionable (not 'unbound variable')",
          "unbound variable" not in out and ("CLAUDE_CONFIG_DIR" in out or "global config directory" in out),
          last_line(out))

    print()
    passed = sum(1 for _, cond, _ in RESULTS if cond)
    print(f"=== {passed}/{len(RESULTS)} e2e install-path checks passed ===")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

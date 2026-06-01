#!/usr/bin/env python3
"""Verify the compiled revue wheel cannot honour source-tree licence bypasses."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
REVUE_CORE = REPO_ROOT / "packaging" / "revue_core"
SOURCE_LOCAL_RUN = REPO_ROOT / "scripts" / "local_run.py"
SKIP_ENV_VAR = "REVUE_SKIP_LICENCE_CHECK"


def _fail(message: str, result: subprocess.CompletedProcess[str] | None = None) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    if result is not None:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return _fail("usage: verify_wheel_licence_gate.py <revue-wheel.whl>")

    wheel = Path(args[0]).resolve()
    if not wheel.is_file():
        return _fail(f"wheel not found: {wheel}")

    with tempfile.TemporaryDirectory(prefix="revue-370-wheel-gate-") as temp_dir:
        temp_root = Path(temp_dir)
        venv_dir = temp_root / "venv"
        empty_home = temp_root / "home"
        empty_home.mkdir()
        venv.create(venv_dir, with_pip=True)

        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        pip = bin_dir / ("pip.exe" if os.name == "nt" else "pip")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        revue = bin_dir / ("revue.exe" if os.name == "nt" else "revue")

        install_result = subprocess.run(
            [str(pip), "install", "--quiet", "-e", str(REVUE_CORE), str(wheel)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        if install_result.returncode != 0:
            # Route through _fail so pip's captured stdout/stderr is surfaced.
            # check=True would raise CalledProcessError whose captured output
            # Python's default handler discards, hiding the real cause behind a
            # bare traceback on a release-blocking gate.
            return _fail(
                "pip install of revue_core + wheel failed in the gate venv",
                install_result,
            )

        env = {**os.environ, "HOME": str(empty_home), SKIP_ENV_VAR: "1"}
        wheel_jobs = temp_root / "wheel-jobs"
        packaged_result = subprocess.run(
            [
                str(revue),
                "local-run",
                "prepare",
                "--base",
                "main",
                "--jobs-dir",
                str(wheel_jobs),
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        if packaged_result.returncode == 0:
            return _fail(
                f"compiled wheel honoured {SKIP_ENV_VAR}=1 and bypassed the licence gate",
                packaged_result,
            )
        if "revue activate" not in packaged_result.stderr:
            return _fail(
                "compiled wheel failed without the expected licence-required error",
                packaged_result,
            )

        source_jobs = temp_root / "source-jobs"
        source_result = subprocess.run(
            [
                str(python),
                str(SOURCE_LOCAL_RUN),
                "prepare",
                "--base",
                "HEAD",
                "--jobs-dir",
                str(source_jobs),
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        # Gate-specific signal, NOT prepare's exit code: with an empty HOME the
        # licence gate emits the "revue activate" error and exits 8 *unless* the
        # source-tree bypass skipped it. Asserting returncode==0 would conflate
        # "bypass honoured" with "prepare happened to succeed", so a benign
        # git/diff hiccup in CI could false-fail this release-blocking gate.
        if "revue activate" in source_result.stderr:
            return _fail(
                f"source-tree developer mode no longer honours {SKIP_ENV_VAR}=1 "
                "(licence gate fired instead of being bypassed)",
                source_result,
            )

    print("REVUE-370 wheel licence-gate verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

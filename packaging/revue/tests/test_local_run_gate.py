"""REVUE-278 Task 7 — wire-up of licence validation into local_run.py.

The gate must:
- Run BEFORE any review subcommand (prepare / consolidate / run / vex / verdicts)
- Be skipped for the developer ``position`` subcommand
- Strip ``REVUE_SKIP_LICENCE_CHECK=1`` from the vendored wheel copy
- Return validate_licence's exit code unchanged (don't swallow non-zero)
- Block (exit 8) when ``~/.config/revue/licence.jwt`` is missing or empty
"""
from __future__ import annotations

import pytest


@pytest.fixture
def _gate():
    from revue_skill.skill.local_run import _gate_licence_validation
    return _gate_licence_validation


def test_position_subcommand_skips_validation(monkeypatch, _gate):
    """Position is a dev/CI fixture-runner — no licence needed."""
    called = []
    monkeypatch.setattr(
        "revue_skill.validate.validate_licence",
        lambda jwt: called.append(jwt) or 0,
    )
    assert _gate("position") == 0
    assert called == []


@pytest.mark.parametrize("cmd", [
    "prepare", "consolidate", "run",
    "classify-and-build-vex-jobs", "apply-verdicts-and-finalize",
])
def test_review_subcommands_invoke_validate_licence(
    monkeypatch, tmp_path, cmd, _gate
):
    """Every review subcommand must run validate_licence with the JWT
    contents from ~/.config/revue/licence.jwt."""
    licence_dir = tmp_path / ".config" / "revue"
    licence_dir.mkdir(parents=True)
    licence_file = licence_dir / "licence.jwt"
    licence_file.write_text("test.jwt.token")

    monkeypatch.setattr("revue_skill.skill.local_run.Path.home", lambda: tmp_path)

    called = []
    monkeypatch.setattr(
        "revue_skill.validate.validate_licence",
        lambda jwt: called.append(jwt) or 0,
    )

    assert _gate(cmd) == 0
    assert called == ["test.jwt.token"], (
        f"validate_licence not invoked for {cmd}; got {called}"
    )


def test_review_subcommand_propagates_nonzero_exit(monkeypatch, tmp_path, _gate):
    """A non-zero exit from validate_licence (e.g. 8 for AC4 block) must
    surface to the caller unchanged — the gate cannot mask failures."""
    licence_dir = tmp_path / ".config" / "revue"
    licence_dir.mkdir(parents=True)
    (licence_dir / "licence.jwt").write_text("jwt")
    monkeypatch.setattr("revue_skill.skill.local_run.Path.home", lambda: tmp_path)

    monkeypatch.setattr(
        "revue_skill.validate.validate_licence",
        lambda jwt: 8,
    )
    assert _gate("prepare") == 8


def test_env_var_bypass_removed_in_wheel(monkeypatch, tmp_path, _gate, capsys):
    """REVUE-370: REVUE_SKIP_LICENCE_CHECK MUST NOT bypass the gate in the wheel.

    Threat model: a paying customer with shell access could set
    ``REVUE_SKIP_LICENCE_CHECK=1`` and issue unlimited free reviews against the
    published Nuitka wheel. The dev-mirror ``scripts/local_run.py`` keeps the
    bypass for source-tree testing, but the vendored copy that ships in the
    wheel must enforce the licence gate regardless of the environment.

    With the env var set and ``Path.home`` pointed at an empty tmp dir, the
    gate must fall through to the missing-licence-file branch and exit 8 —
    *not* return 0. The ``Path.home`` monkeypatch is load-bearing: without it
    the test would non-deterministically read the developer's real
    ``~/.config/revue/licence.jwt`` on CI/dev machines.
    """
    monkeypatch.setattr("revue_skill.skill.local_run.Path.home", lambda: tmp_path)
    monkeypatch.setenv("REVUE_SKIP_LICENCE_CHECK", "1")
    monkeypatch.setattr(
        "revue_skill.validate.validate_licence",
        lambda jwt: pytest.fail(
            "validate_licence should not be reached — gate must exit on missing licence first"
        ),
    )
    assert _gate("prepare") == 8, (
        "REVUE_SKIP_LICENCE_CHECK=1 bypassed the gate in the vendored copy — "
        "this is a licence-bypass exploit. The bypass must be stripped at "
        "vendor time via sources.yaml rewrite_imports."
    )
    err = capsys.readouterr().err
    assert "revue activate" in err


def test_vendored_source_text_strips_skip_env_var():
    """REVUE-370 differential proof: the vendored copy must NOT contain the
    REVUE_SKIP_LICENCE_CHECK literal, while the source-tree copy MUST keep it.

    This is a cheap text-level guard that catches accidental regressions of
    the ``rewrite_imports`` entry in ``packaging/revue/tools/sources.yaml``
    without standing up the full source-tree import path. It also documents
    the dev/wheel split: the env-var bypass is a *dev convenience* that the
    vendor step surgically removes for the shipped artifact.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    vendored = repo_root / "packaging" / "revue" / "src" / "revue_skill" / "skill" / "local_run.py"
    source_tree = repo_root / "scripts" / "local_run.py"

    vendored_text = vendored.read_text()
    source_text = source_tree.read_text()

    assert "REVUE_SKIP_LICENCE_CHECK" not in vendored_text, (
        f"{vendored} still contains REVUE_SKIP_LICENCE_CHECK — the vendor "
        f"rewrite in packaging/revue/tools/sources.yaml did not strip it. "
        f"Regenerate via `python packaging/revue/tools/vendor_sources.py --clean`."
    )
    assert "REVUE_SKIP_LICENCE_CHECK" in source_text, (
        f"{source_tree} no longer contains REVUE_SKIP_LICENCE_CHECK — the "
        f"source-tree dev bypass must be preserved for local development. "
        f"If you intentionally removed it, update this test and document why."
    )


def test_missing_licence_file_blocks(monkeypatch, tmp_path, _gate, capsys):
    """No licence.jwt → exit 8 with `revue activate` guidance."""
    monkeypatch.setattr("revue_skill.skill.local_run.Path.home", lambda: tmp_path)
    monkeypatch.delenv("REVUE_SKIP_LICENCE_CHECK", raising=False)

    assert _gate("prepare") == 8
    err = capsys.readouterr().err
    assert "revue activate" in err


def test_empty_licence_file_blocks(monkeypatch, tmp_path, _gate, capsys):
    """An empty (truncated) licence.jwt → exit 8."""
    licence_dir = tmp_path / ".config" / "revue"
    licence_dir.mkdir(parents=True)
    (licence_dir / "licence.jwt").write_text("   \n")
    monkeypatch.setattr("revue_skill.skill.local_run.Path.home", lambda: tmp_path)
    monkeypatch.delenv("REVUE_SKIP_LICENCE_CHECK", raising=False)

    assert _gate("prepare") == 8
    err = capsys.readouterr().err
    assert "revue activate" in err


def test_validate_import_failure_does_not_bypass(monkeypatch, tmp_path, _gate, capsys):
    """If ``revue_skill.validate`` cannot be imported, the gate must HARD
    FAIL — not silently return 0.

    Threat model: in the published wheel, ``validate.so`` is shipped
    alongside ``local_run.so``. If an attacker tampers with the install (e.g.
    deletes validate.so to force ImportError), an ImportError-as-bypass would
    grant unlimited reviews. The packaged copy must therefore exit 8 with a
    re-install instruction on import failure — the dev-mirror in
    ``scripts/local_run.py`` keeps the source-tree bypass, but the wheel
    copy must not.
    """
    # Seed a real licence file so we reach the validate import (not the
    # earlier "missing file → exit 8" branch).
    licence_dir = tmp_path / ".config" / "revue"
    licence_dir.mkdir(parents=True)
    (licence_dir / "licence.jwt").write_text("eyJfake.token.value")
    monkeypatch.setattr("revue_skill.skill.local_run.Path.home", lambda: tmp_path)
    monkeypatch.delenv("REVUE_SKIP_LICENCE_CHECK", raising=False)

    # Force the import inside _gate_licence_validation to fail.
    import sys
    monkeypatch.setitem(sys.modules, "revue_skill.validate", None)

    assert _gate("prepare") == 8, (
        "ImportError on validate must NOT bypass to 0 — that would be a "
        "licence-bypass exploit in the compiled wheel."
    )
    err = capsys.readouterr().err
    assert "Revue installation appears corrupt" in err
    assert "reinstall" in err.lower() or "Reinstall" in err


def test_build_compiles_every_jwt_touching_module():
    """IP-PROTECTION INVARIANT: every module that touches a JWT (sign,
    verify, embedded public key, validation URL, exit codes) MUST be in
    ``packaging/revue/build/build_nuitka.py:COMPILE_ROOTS``.

    A plain ``.py`` in the wheel is a customer-readable bypass surface — the
    hardcoded ``VALIDATE_URL`` is one edit away from a free-review exploit.

    Failing this test means a new licence-touching module was added without
    a corresponding Nuitka build entry, and the published wheel would ship
    the customer plain source for it. Add the module to ``COMPILE_ROOTS``
    and update this test's expected set.
    """
    import importlib.util
    from pathlib import Path

    build_script = (
        Path(__file__).resolve().parents[2] / "revue" / "build" / "build_nuitka.py"
    )
    spec = importlib.util.spec_from_file_location("build_nuitka", build_script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    compiled_basenames = {p.name for p in mod.COMPILE_ROOTS}
    required = {"activate.py", "validate.py", "local_run.py"}
    missing = required - compiled_basenames
    assert not missing, (
        f"COMPILE_ROOTS is missing licence-touching modules: {sorted(missing)}. "
        f"Currently lists: {sorted(compiled_basenames)}. "
        f"Add the missing entries to packaging/revue/build/build_nuitka.py."
    )

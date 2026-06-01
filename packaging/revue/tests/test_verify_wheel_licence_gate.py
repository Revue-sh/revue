"""REVUE-370 release-gate tests for the compiled revue wheel verifier."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "verify_wheel_licence_gate.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_wheel_licence_gate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_main_with_built_wheel_checks_packaged_block_and_source_tree_bypass(
    monkeypatch, tmp_path
):
    # Arrange
    module = _load_script()
    wheel = tmp_path / "revue-0.1.0-cp312-cp312-macosx_14_0_arm64.whl"
    wheel.touch()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0].endswith("pip"):
            return module.subprocess.CompletedProcess(command, 0, "", "")
        if command[0].endswith("revue"):
            return module.subprocess.CompletedProcess(
                command,
                8,
                "",
                "error: Revue needs an activated licence - run `revue activate`.",
            )
        return module.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.venv, "create", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    # Act
    result = module.main([str(wheel)])

    # Assert
    assert result == 0
    assert len(calls) == 3
    wheel_command, wheel_kwargs = calls[1]
    source_command, source_kwargs = calls[2]
    assert wheel_command[1:3] == ["local-run", "prepare"]
    assert source_command[1].endswith("scripts/local_run.py")
    assert wheel_kwargs["env"]["REVUE_SKIP_LICENCE_CHECK"] == "1"
    assert source_kwargs["env"]["REVUE_SKIP_LICENCE_CHECK"] == "1"


def test_main_when_packaged_wheel_honours_bypass_fails_release_gate(
    monkeypatch, tmp_path
):
    # Arrange
    module = _load_script()
    wheel = tmp_path / "revue-0.1.0-cp312-cp312-manylinux_2_17_x86_64.whl"
    wheel.touch()

    def fake_run(command, **kwargs):
        return module.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.venv, "create", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    # Act
    result = module.main([str(wheel)])

    # Assert
    assert result == 1


def test_source_tree_gate_firing_fails_release_gate(monkeypatch, tmp_path):
    """REVUE-370 M2: if the source-tree run emits the licence-required error,
    the dev bypass has regressed and the gate must fail — independent of the
    source run's exit code."""
    # Arrange
    module = _load_script()
    wheel = tmp_path / "revue-0.1.0-cp312-cp312-macosx_14_0_arm64.whl"
    wheel.touch()
    activate_err = "error: Revue needs an activated licence - run `revue activate`."

    def fake_run(command, **kwargs):
        if command[0].endswith("pip"):
            return module.subprocess.CompletedProcess(command, 0, "", "")
        if command[0].endswith("revue"):
            # wheel correctly blocks
            return module.subprocess.CompletedProcess(command, 8, "", activate_err)
        # source-tree run exits 0 but the gate fired — bypass regressed
        return module.subprocess.CompletedProcess(command, 0, "", activate_err)

    monkeypatch.setattr(module.venv, "create", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    # Act
    result = module.main([str(wheel)])

    # Assert
    assert result == 1


def test_source_tree_nonzero_exit_without_gate_error_still_passes(monkeypatch, tmp_path):
    """REVUE-370 M2: a non-licence failure in the source-tree run (e.g. a git
    hiccup) must NOT false-fail the gate, since it does not prove a bypass
    regression."""
    # Arrange
    module = _load_script()
    wheel = tmp_path / "revue-0.1.0-cp312-cp312-macosx_14_0_arm64.whl"
    wheel.touch()
    activate_err = "error: Revue needs an activated licence - run `revue activate`."

    def fake_run(command, **kwargs):
        if command[0].endswith("pip"):
            return module.subprocess.CompletedProcess(command, 0, "", "")
        if command[0].endswith("revue"):
            return module.subprocess.CompletedProcess(command, 8, "", activate_err)
        # source-tree run failed for an unrelated reason — no licence error
        return module.subprocess.CompletedProcess(command, 1, "", "fatal: not a git repository")

    monkeypatch.setattr(module.venv, "create", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    # Act
    result = module.main([str(wheel)])

    # Assert
    assert result == 0


def test_pip_install_failure_fails_release_gate(monkeypatch, tmp_path):
    """REVUE-370 M3: a pip install failure surfaces via _fail (return 1), not a
    bare CalledProcessError traceback that hides pip's diagnostic."""
    # Arrange
    module = _load_script()
    wheel = tmp_path / "revue-0.1.0-cp312-cp312-macosx_14_0_arm64.whl"
    wheel.touch()

    def fake_run(command, **kwargs):
        if command[0].endswith("pip"):
            return module.subprocess.CompletedProcess(
                command, 1, "", "ERROR: no matching distribution"
            )
        return module.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.venv, "create", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    # Act
    result = module.main([str(wheel)])

    # Assert
    assert result == 1


def test_tag_skill_builds_run_compiled_wheel_licence_gate_verifier():
    # Arrange
    pipeline = (
        Path(__file__).resolve().parents[3] / "bitbucket-pipelines.yml"
    ).read_text()

    # Act
    verifier_calls = pipeline.count(
        "packaging/revue/tools/verify_wheel_licence_gate.py "
        "packaging/revue/dist/wheels/*.whl"
    )

    # Assert
    assert verifier_calls == 2

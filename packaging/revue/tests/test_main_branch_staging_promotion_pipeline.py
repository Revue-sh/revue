"""REVUE-348 - main branch deploys staging first, then gates production."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
PIPELINES_FILE = REPO_ROOT / "bitbucket-pipelines.yml"


def _load_pipeline() -> dict[str, Any]:
    return yaml.safe_load(PIPELINES_FILE.read_text(encoding="utf-8"))


def _flatten_steps(entries: list[Any]) -> list[dict[str, Any]]:
    """Flatten a pipeline entry list, unwrapping ``parallel`` blocks (both the
    list form and the ``{steps: [...]}`` form) into their constituent steps,
    preserving order."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if "step" in entry:
            out.append(entry["step"])
        elif "parallel" in entry:
            par = entry["parallel"]
            items = par.get("steps", []) if isinstance(par, dict) else par
            out.extend(s["step"] for s in items if isinstance(s, dict) and "step" in s)
    return out


def _main_steps() -> list[dict[str, Any]]:
    pipeline = _load_pipeline()
    return _flatten_steps(pipeline["pipelines"]["branches"]["main"])


def _step_named(name: str) -> dict[str, Any]:
    for step in _main_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"main pipeline is missing step named {name!r}")


def _step_names() -> list[str]:
    return [step.get("name", "") for step in _main_steps()]


def _script_text(step: dict[str, Any]) -> str:
    return "\n".join(str(line) for line in step.get("script", []))


def _main_script_text() -> str:
    return "\n".join(_script_text(step) for step in _main_steps())


def test_main_branch_pipeline_promotes_single_image_from_staging_to_prod() -> None:
    # Arrange
    expected_order = [
        "Run Tests",
        "Run Web Tests",  # REVUE-393: web suite gates the build/deploy chain
        "Build Web Image → Fly Registry",
        "Deploy Web → Staging",
        "Smoke Test → Staging",
        "Deploy Web → Production",
        "Smoke Test → Production",
        "Tag Release (if warranted)",
    ]

    # Act
    names = _step_names()

    # Assert
    assert names == expected_order, (
        "main pipeline must have exactly one gated web promotion chain before "
        f"release tagging; actual names={names}"
    )


def test_web_image_builds_and_pushes_commit_tag_exactly_once() -> None:
    # Arrange
    step = _step_named("Build Web Image → Fly Registry")

    # Act
    script = _script_text(step)
    main_script = _main_script_text()

    # Assert
    assert "registry.fly.io/revue-staging:${BITBUCKET_COMMIT}" in script
    assert "flyctl auth docker" in script
    assert main_script.count("docker build") == 1
    assert main_script.count("docker push") == 1
    assert "src/web" in script


def test_fly_steps_continue_to_use_fly_api_token_variable() -> None:
    # Arrange
    fly_steps = [
        _step_named("Build Web Image → Fly Registry"),
        _step_named("Deploy Web → Staging"),
        _step_named("Deploy Web → Production"),
    ]

    # Act
    scripts = [_script_text(step) for step in fly_steps]
    main_script = "\n".join(scripts)

    # Assert
    for script in scripts:
        assert 'test -n "${FLY_API_TOKEN:-}"' in script
    assert "FLY_ACCESS_TOKEN" not in main_script
    assert "FLY_TOKEN" not in main_script


def test_fly_installer_fetches_fail_fast_before_execution() -> None:
    # Arrange
    fly_steps = [
        _step_named("Build Web Image → Fly Registry"),
        _step_named("Deploy Web → Staging"),
        _step_named("Deploy Web → Production"),
    ]

    # Act
    scripts = [_script_text(step) for step in fly_steps]

    # Assert
    for script in scripts:
        assert "curl -fsSL https://fly.io/install.sh -o /tmp/fly-install.sh" in script
        assert "&& sh /tmp/fly-install.sh" in script
        assert "curl -L https://fly.io/install.sh | sh" not in script


def test_staging_deploy_uses_staging_config_and_commit_image() -> None:
    # Arrange
    step = _step_named("Deploy Web → Staging")

    # Act
    script = _script_text(step)

    # Assert
    assert "--app revue-staging" in script
    assert "--config fly.staging.toml" in script
    assert 'WEB_IMAGE="registry.fly.io/revue-staging:${BITBUCKET_COMMIT}"' in script
    assert '--image "$WEB_IMAGE"' in script
    assert "--remote-only" in script


def test_production_deploy_is_manual_and_uses_same_commit_image_without_rebuild() -> None:
    # Arrange
    step = _step_named("Deploy Web → Production")

    # Act
    script = _script_text(step)

    # Assert
    assert step.get("trigger") == "manual"
    assert "--app revue-io" in script
    assert "--config fly.toml" in script
    assert 'WEB_IMAGE="registry.fly.io/revue-staging:${BITBUCKET_COMMIT}"' in script
    assert '--image "$WEB_IMAGE"' in script
    assert "docker build" not in script
    assert "flyctl deploy --app revue-io" in script


def test_staging_smoke_tests_gate_health_and_license_validation_paths() -> None:
    # Arrange
    step = _step_named("Smoke Test → Staging")

    # Act
    script = _script_text(step)

    # Assert
    assert "https://staging.revue.sh/health" in script
    assert '{"status": "ok"}' in script
    assert "https://api.staging.revue.sh/health" in script
    assert "https://api.staging.revue.sh/license/validate" in script
    assert "https://staging.revue.sh/api/license/validate" in script
    assert 'code="$(curl -sS $CURL_RETRY_FLAGS -o "$out" -w "%{http_code}"' in script
    assert '[ "$code" != "200" ]' in script
    assert "--retry 12 --retry-all-errors --connect-timeout 5 --max-time 20" in script
    assert "422" in script
    assert "exit 1" in script


def test_production_smoke_tests_gate_health_and_license_validation_paths() -> None:
    # Arrange
    step = _step_named("Smoke Test → Production")

    # Act
    script = _script_text(step)

    # Assert
    assert "https://revue.sh/health" in script
    assert '{"status": "ok"}' in script
    assert "https://api.revue.sh/health" in script
    assert "https://api.revue.sh/license/validate" in script
    assert "https://revue.sh/api/license/validate" in script
    assert 'code="$(curl -sS $CURL_RETRY_FLAGS -o "$out" -w "%{http_code}"' in script
    assert '[ "$code" != "200" ]' in script
    assert "--retry 12 --retry-all-errors --connect-timeout 5 --max-time 20" in script
    assert "422" in script
    assert "exit 1" in script

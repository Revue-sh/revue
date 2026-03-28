"""Tests for the GitHub Action definition and entrypoint script (Story [75]).

These tests validate the action.yml structure and entrypoint.sh without
actually running them — structural compliance and required-field checks.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

# Path to the ci-templates/github-actions directory relative to the repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # .../Projects/revue.io
_ACTION_DIR = _REPO_ROOT / "ci-templates" / "github-actions"
_ACTION_YML = _ACTION_DIR / "action.yml"
_ENTRYPOINT = _ACTION_DIR / "entrypoint.sh"


class TestActionYmlExists:
    def test_action_yml_exists(self) -> None:
        assert _ACTION_YML.exists(), f"action.yml not found at {_ACTION_YML}"

    def test_entrypoint_sh_exists(self) -> None:
        assert _ENTRYPOINT.exists(), f"entrypoint.sh not found at {_ENTRYPOINT}"

    def test_entrypoint_sh_is_executable(self) -> None:
        mode = os.stat(_ENTRYPOINT).st_mode
        assert bool(mode & stat.S_IXUSR), "entrypoint.sh is not executable"


class TestActionYmlStructure:
    @pytest.fixture(scope="class")
    def action(self) -> dict:
        with open(_ACTION_YML) as f:
            return yaml.safe_load(f)

    def test_has_name(self, action) -> None:
        assert "name" in action
        assert "Revue" in action["name"]

    def test_has_description(self, action) -> None:
        assert "description" in action
        assert len(action["description"]) > 10

    def test_has_branding(self, action) -> None:
        assert "branding" in action
        assert "icon" in action["branding"]
        assert "color" in action["branding"]

    def test_has_inputs(self, action) -> None:
        assert "inputs" in action

    def test_required_input_ai_api_key(self, action) -> None:
        inputs = action["inputs"]
        assert "ai_api_key" in inputs
        assert inputs["ai_api_key"]["required"] is True

    def test_optional_inputs_have_defaults(self, action) -> None:
        inputs = action["inputs"]
        for name, spec in inputs.items():
            if not spec.get("required", False):
                assert "default" in spec, f"Optional input '{name}' missing default"

    def test_has_outputs(self, action) -> None:
        assert "outputs" in action
        outputs = action["outputs"]
        assert "findings_count" in outputs
        assert "critical_count" in outputs

    def test_runs_as_composite(self, action) -> None:
        assert action["runs"]["using"] == "composite"

    def test_composite_has_steps(self, action) -> None:
        steps = action["runs"]["steps"]
        assert len(steps) >= 3

    def test_inputs_include_all_ac_required(self, action) -> None:
        """AC requires: revue_token, ai_api_key, ai_provider, ai_model, mode."""
        inputs = action["inputs"]
        required_inputs = ["revue_token", "ai_api_key", "ai_provider", "ai_model", "mode"]
        for inp in required_inputs:
            assert inp in inputs, f"Missing required input: {inp}"


class TestWorkflowTemplateUsesAction:
    """The .github/workflows/revue-review.yml must reference revue-io/action@v1."""

    @pytest.fixture(scope="class")
    def workflow(self) -> dict:
        wf_path = _REPO_ROOT / ".github" / "workflows" / "revue-review.yml"
        assert wf_path.exists(), f"Workflow not found at {wf_path}"
        with open(wf_path) as f:
            return yaml.safe_load(f)

    def test_workflow_triggers_on_pull_request(self, workflow) -> None:
        # PyYAML parses 'on' as boolean True — use True as key
        on_triggers = workflow.get("on", workflow.get(True, {}))
        assert "pull_request" in on_triggers

    def test_workflow_uses_revue_action(self, workflow) -> None:
        """At least one step must use revue-io/action@v1."""
        steps = []
        for job in workflow.get("jobs", {}).values():
            steps.extend(job.get("steps", []))
        uses_revue = any(
            "revue-io/action" in str(step.get("uses", ""))
            for step in steps
        )
        assert uses_revue, "No step uses revue-io/action — update workflow template"

    def test_workflow_has_pull_requests_write_permission(self, workflow) -> None:
        permissions = workflow.get("permissions", {})
        assert permissions.get("pull-requests") == "write"


class TestEntrypointShContent:
    @pytest.fixture(scope="class")
    def script(self) -> str:
        return _ENTRYPOINT.read_text()

    def test_has_shebang(self, script) -> None:
        assert script.startswith("#!/")

    def test_set_euo_pipefail(self, script) -> None:
        assert "set -euo pipefail" in script

    def test_validates_ai_api_key(self, script) -> None:
        assert "AI_API_KEY" in script

    def test_handles_empty_diff(self, script) -> None:
        assert "empty" in script.lower() or "DIFF_FILE" in script

    def test_emits_github_outputs(self, script) -> None:
        assert "GITHUB_OUTPUT" in script
        assert "findings_count" in script
        assert "critical_count" in script

    def test_fail_on_critical_logic(self, script) -> None:
        assert "REVUE_FAIL_ON_CRITICAL" in script
        assert "critical" in script.lower()

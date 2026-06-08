#!/usr/bin/env python3
"""Tests for .revue.yml config loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from revue_core.core.config_loader import load_config, validate_config
from revue_core.core.ai_config import AIConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yml(tmp_path: Path, content: str) -> str:
    """Write YAML content to a .revue.yml in *tmp_path* and return its path."""
    p = tmp_path / ".revue.yml"
    p.write_text(content)
    return str(p)


def _minimal_yml() -> str:
    return 'version: "1"\nai:\n  provider: anthropic\n'


# ---------------------------------------------------------------------------
# 1. Missing file falls back to env
# ---------------------------------------------------------------------------

def test_load_config_missing_file_falls_back_to_env(tmp_path: Path) -> None:
    nonexistent = str(tmp_path / "no_such_file.yml")
    config = load_config(config_path=nonexistent)
    assert isinstance(config, AIConfig)
    # Should match from_env defaults — REVUE-267: default provider is now openrouter
    assert config.provider == os.getenv("REVUE_PROVIDER", "openrouter")


# ---------------------------------------------------------------------------
# 2. Minimal valid config
# ---------------------------------------------------------------------------

def test_load_config_minimal_valid(tmp_path: Path) -> None:
    path = _write_yml(tmp_path, _minimal_yml())
    config = load_config(config_path=path)
    assert config.provider == "anthropic"
    # defaults should still be populated
    assert config.max_diff_lines == 2000
    assert config.min_confidence == 70


# ---------------------------------------------------------------------------
# 3. Full schema mapping
# ---------------------------------------------------------------------------

def test_load_config_full_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear env vars that would override YAML values
    for var in ("REVUE_PROVIDER", "REVUE_MODEL", "REVUE_BASE_URL", "REVUE_API_KEY_ENV"):
        monkeypatch.delenv(var, raising=False)

    full_yml = """\
version: "1"

ai:
  provider: openai
  model: gpt-4o
  api_key_env: MY_KEY
  base_url: "https://my-proxy.example.com/v1"
  temperature: 0.7
  max_tokens: 8192
  azure:
    endpoint: "https://my-azure.openai.azure.com"
    deployment: my-deploy
    api_version: "2024-06-01"

review:
  max_diff_lines: 5000
  min_confidence: 85
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "vendor/*"

agents:
  team: team-security
  custom_agents_dir: ./agents

output:
  format: json
  file: review-output.json

# gpt-4o is not a Revue-vetted model; declare it as an unsupported
# customer-extended entry so the per-model registry gate accepts it.
models:
  gpt-4o:
    provider: openai
    schema_mode: response_format
    schema_strict: true
    tool_choice_first_turn: auto
    max_tokens_default: 8192
    tier: unsupported
"""
    path = _write_yml(tmp_path, full_yml)
    config = load_config(config_path=path)

    assert config.provider == "openai"
    assert config.model == "gpt-4o"
    assert config.api_key_env == "MY_KEY"
    assert config.base_url == "https://my-proxy.example.com/v1"
    assert config.ai_temp == 0.7
    assert config.ai_max_tokens == 8192
    assert config.azure_endpoint == "https://my-azure.openai.azure.com"
    assert config.azure_deployment == "my-deploy"
    assert config.azure_api_version == "2024-06-01"
    assert config.max_diff_lines == 5000
    assert config.min_confidence == 85
    assert config.ignore_patterns == ["*.md", "*.lock", "vendor/*"]
    assert config.agents_team == "team-security"
    assert config.custom_agents_dir == "./agents"
    assert config.output_format == "json"
    assert config.output_file == "review-output.json"


# ---------------------------------------------------------------------------
# 4. Invalid version
# ---------------------------------------------------------------------------

def test_load_config_invalid_version(tmp_path: Path) -> None:
    path = _write_yml(tmp_path, 'version: "2"\nai:\n  provider: anthropic\n')
    with pytest.raises(ValueError, match="unsupported version"):
        load_config(config_path=path)


# ---------------------------------------------------------------------------
# 5. Missing version
# ---------------------------------------------------------------------------

def test_load_config_missing_version(tmp_path: Path) -> None:
    path = _write_yml(tmp_path, "ai:\n  provider: anthropic\n")
    with pytest.raises(ValueError, match="missing required field 'version'"):
        load_config(config_path=path)


# ---------------------------------------------------------------------------
# 6. Overrides applied
# ---------------------------------------------------------------------------

def test_load_config_overrides_applied(tmp_path: Path) -> None:
    path = _write_yml(tmp_path, _minimal_yml())
    config = load_config(config_path=path, overrides={"max_diff_lines": 500})
    assert config.max_diff_lines == 500


# ---------------------------------------------------------------------------
# 7. Env takes precedence
# ---------------------------------------------------------------------------

def test_load_config_env_takes_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yml = 'version: "1"\nai:\n  provider: openai\n'
    path = _write_yml(tmp_path, yml)

    monkeypatch.setenv("REVUE_PROVIDER", "anthropic")
    config = load_config(config_path=path)
    assert config.provider == "anthropic"


# ---------------------------------------------------------------------------
# 8. Validate — valid config
# ---------------------------------------------------------------------------

def test_validate_config_valid() -> None:
    config = AIConfig.from_env()
    config.provider = "anthropic"  # type: ignore[assignment]
    config.max_diff_lines = 2000
    config.min_confidence = 70
    config.ai_temp = 0.3
    errors = validate_config(config)
    assert errors == []


# ---------------------------------------------------------------------------
# 9. Validate — unknown provider
# ---------------------------------------------------------------------------

def test_validate_config_unknown_provider() -> None:
    config = AIConfig.from_env()
    config.provider = "gemini"  # type: ignore[assignment]
    errors = validate_config(config)
    assert any("Unknown provider" in e for e in errors)


# ---------------------------------------------------------------------------
# 10. Validate — azure missing endpoint
# ---------------------------------------------------------------------------

def test_validate_config_azure_missing_endpoint() -> None:
    config = AIConfig.from_env()
    config.provider = "azure"  # type: ignore[assignment]
    config.azure_endpoint = ""
    config.azure_deployment = ""
    errors = validate_config(config)
    assert any("azure_endpoint" in e for e in errors)


# ---------------------------------------------------------------------------
# 11. Validate — max_diff_lines zero
# ---------------------------------------------------------------------------

def test_validate_config_max_diff_lines_zero() -> None:
    config = AIConfig.from_env()
    config.max_diff_lines = 0
    errors = validate_config(config)
    assert any("max_diff_lines" in e for e in errors)


# ---------------------------------------------------------------------------
# 12. Validate — confidence out of range
# ---------------------------------------------------------------------------

def test_validate_config_confidence_out_of_range() -> None:
    config = AIConfig.from_env()
    config.min_confidence = 150
    errors = validate_config(config)
    assert any("min_confidence" in e for e in errors)


# ---------------------------------------------------------------------------
# REVUE-340: agent_timeout_seconds validator cap raised 600 → 1800
# ---------------------------------------------------------------------------

def test_validate_config_agent_timeout_lower_bound_accepts_1s() -> None:
    config = AIConfig.from_env()
    config.agent_timeout_seconds = 1
    errors = validate_config(config)
    assert not any("agent_timeout_seconds" in e for e in errors)


def test_validate_config_agent_timeout_accepts_600s_unchanged() -> None:
    config = AIConfig.from_env()
    config.agent_timeout_seconds = 600
    errors = validate_config(config)
    assert not any("agent_timeout_seconds" in e for e in errors)


def test_validate_config_agent_timeout_upper_bound_accepts_1800s() -> None:
    config = AIConfig.from_env()
    config.agent_timeout_seconds = 1800
    errors = validate_config(config)
    assert not any("agent_timeout_seconds" in e for e in errors)


def test_validate_config_agent_timeout_rejects_1801s() -> None:
    config = AIConfig.from_env()
    config.agent_timeout_seconds = 1801
    errors = validate_config(config)
    assert any("agent_timeout_seconds" in e for e in errors)
    assert any("1800" in e for e in errors if "agent_timeout_seconds" in e)


def test_validate_config_agent_timeout_rejects_zero() -> None:
    config = AIConfig.from_env()
    config.agent_timeout_seconds = 0
    errors = validate_config(config)
    assert any("agent_timeout_seconds" in e for e in errors)


# ---------------------------------------------------------------------------
# REVUE-94: Pattern support in noise_filters
# ---------------------------------------------------------------------------

def test_yaml_parser_reads_allowed_patterns(tmp_path: Path) -> None:
    """AC1: Parser reads allowed_patterns with pattern and rationale fields."""
    yml = """\
version: "1"
ai:
  provider: anthropic
noise_filters:
  allowed_patterns:
    - pattern: "_def attribute access on LoadedAgent"
      rationale: "Internal implementation detail, no public API"
    - pattern: "Inline lazy httpx import"
      rationale: "Intentional lazy loading pattern"
"""
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert len(config.allowed_patterns) == 2
    assert config.allowed_patterns[0]["pattern"] == "_def attribute access on LoadedAgent"
    assert config.allowed_patterns[0]["rationale"] == "Internal implementation detail, no public API"
    assert config.allowed_patterns[1]["pattern"] == "Inline lazy httpx import"


def test_yaml_parser_reads_disallowed_patterns(tmp_path: Path) -> None:
    """AC1: Parser reads disallowed_patterns with pattern and rationale fields."""
    yml = """\
version: "1"
ai:
  provider: anthropic
noise_filters:
  disallowed_patterns:
    - pattern: "TODO comments in production code"
      rationale: "TODOs should be tracked as Jira tickets"
"""
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert len(config.disallowed_patterns) == 1
    assert config.disallowed_patterns[0]["pattern"] == "TODO comments in production code"
    assert config.disallowed_patterns[0]["rationale"] == "TODOs should be tracked as Jira tickets"


def test_yaml_parser_backward_compatible(tmp_path: Path) -> None:
    """AC1: Existing configs without pattern keys still work — empty lists, no error."""
    path = _write_yml(tmp_path, _minimal_yml())
    config = load_config(config_path=path)
    assert config.allowed_patterns == []
    assert config.disallowed_patterns == []


def test_yaml_parser_rejects_invalid_pattern(tmp_path: Path) -> None:
    """AC1: Pattern entry missing 'pattern' key produces a clear validation error."""
    yml = """\
version: "1"
ai:
  provider: anthropic
noise_filters:
  allowed_patterns:
    - rationale: "Missing the pattern key"
"""
    path = _write_yml(tmp_path, yml)
    with pytest.raises(ValueError, match="pattern"):
        load_config(config_path=path)


def test_yaml_parser_rejects_non_string_pattern(tmp_path: Path) -> None:
    """AC1: Non-string pattern value produces a validation error."""
    yml = """\
version: "1"
ai:
  provider: anthropic
noise_filters:
  allowed_patterns:
    - pattern: 123
      rationale: "Bad type"
"""
    path = _write_yml(tmp_path, yml)
    with pytest.raises(ValueError, match="pattern"):
        load_config(config_path=path)


def test_revue_yml_contains_four_allowed_patterns() -> None:
    """AC3: Project .revue.yml has at least four allowed_patterns with correct content."""
    project_yml = Path(__file__).resolve().parents[4] / ".revue.yml"
    config = load_config(config_path=str(project_yml))
    assert len(config.allowed_patterns) >= 4
    for entry in config.allowed_patterns:
        assert "pattern" in entry
        assert "rationale" in entry
        assert isinstance(entry["pattern"], str)
        assert isinstance(entry["rationale"], str)
    pattern_texts = [e["pattern"] for e in config.allowed_patterns]
    assert "_def attribute access on LoadedAgent" in pattern_texts
    assert "Inline lazy httpx import in pr_description_adapter" in pattern_texts
    assert "test_vcs_adapter.py deletion" in pattern_texts
    assert "Bare except in _inject_pr_context" in pattern_texts


def test_validate_patterns_accepts_optional_applies_to() -> None:
    """applies_to list is accepted and preserved in validated output."""
    from revue_core.core.config_loader import _validate_patterns

    entries = [
        {"pattern": "SRP in models", "rationale": "intentional", "applies_to": ["leo", "maya"]},
    ]
    result = _validate_patterns(entries, "allowed_patterns", ".revue.yml")
    assert result[0]["applies_to"] == ["leo", "maya"]


def test_validate_patterns_without_applies_to_is_backward_compatible() -> None:
    """Pattern without applies_to is still valid — applies to all agents."""
    from revue_core.core.config_loader import _validate_patterns

    entries = [{"pattern": "some pattern", "rationale": "some reason"}]
    result = _validate_patterns(entries, "allowed_patterns", ".revue.yml")
    assert "applies_to" not in result[0]


def test_validate_patterns_rejects_non_list_applies_to() -> None:
    """applies_to must be a list, not a bare string."""
    from revue_core.core.config_loader import _validate_patterns

    entries = [{"pattern": "foo", "rationale": "bar", "applies_to": "leo"}]
    with pytest.raises(ValueError, match="applies_to"):
        _validate_patterns(entries, "allowed_patterns", ".revue.yml")


def test_validate_patterns_rejects_non_string_items_in_applies_to() -> None:
    """All items inside applies_to must be strings."""
    from revue_core.core.config_loader import _validate_patterns

    entries = [{"pattern": "foo", "rationale": "bar", "applies_to": ["leo", 42]}]
    with pytest.raises(ValueError, match="applies_to"):
        _validate_patterns(entries, "allowed_patterns", ".revue.yml")


# ---------------------------------------------------------------------------
# max_parallel_agents — load, default, validate
# ---------------------------------------------------------------------------

def test_load_config_max_parallel_agents_from_yaml(tmp_path: Path) -> None:
    """max_parallel_agents is read from review section and applied to config."""
    yml = """\
version: "1"
ai:
  provider: anthropic
review:
  max_parallel_agents: 3
"""
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.max_parallel_agents == 3


def test_load_config_max_parallel_agents_default() -> None:
    """max_parallel_agents defaults to 1 (sequential) when not specified."""
    config = AIConfig.from_env()
    assert config.max_parallel_agents == 1


def test_validate_config_max_parallel_agents_zero() -> None:
    """max_parallel_agents < 1 produces a validation error."""
    config = AIConfig.from_env()
    config.max_parallel_agents = 0
    errors = validate_config(config)
    assert any("max_parallel_agents" in e for e in errors)


def test_validate_config_max_parallel_agents_too_high() -> None:
    """max_parallel_agents > 10 produces a validation error."""
    config = AIConfig.from_env()
    config.max_parallel_agents = 11
    errors = validate_config(config)
    assert any("max_parallel_agents" in e for e in errors)


def test_load_config_parses_show_reviewed_files_false(tmp_path) -> None:
    """REVUE-134: features.show_reviewed_files: false is parsed into AIConfig."""
    cfg_file = tmp_path / ".revue.yml"
    cfg_file.write_text(
        'version: "1"\nfeatures:\n  show_reviewed_files: false\n'
    )
    from revue_core.core.config_loader import load_config
    config = load_config(config_path=str(cfg_file))
    assert config.show_reviewed_files is False


# ---------------------------------------------------------------------------
# REVUE-166: File type routing configuration (AC4)
# ---------------------------------------------------------------------------


def test_load_config_parses_file_type_routing(tmp_path: Path) -> None:
    """AC4: Parser reads file_type_routing.rules with extensions and reviewers."""
    yml = """\
version: "1"
ai:
  provider: anthropic
file_type_routing:
  rules:
    - extensions: [".yaml", ".yml"]
      reviewers: ["docs-reviewer"]
    - extensions: [".md", ".markdown"]
      reviewers: ["docs-reviewer"]
"""
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert len(config.file_type_routing) == 2
    assert config.file_type_routing[0].extensions == [".yaml", ".yml"]
    assert config.file_type_routing[0].reviewers == ["docs-reviewer"]
    assert config.file_type_routing[1].extensions == [".md", ".markdown"]
    assert config.file_type_routing[1].reviewers == ["docs-reviewer"]


def test_load_config_file_type_routing_empty_reviewers(tmp_path: Path) -> None:
    """AC4: empty reviewers list signals fall-through to existing algorithm."""
    yml = """\
version: "1"
ai:
  provider: anthropic
file_type_routing:
  rules:
    - extensions: [".txt"]
      reviewers: []
"""
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert len(config.file_type_routing) == 1
    assert config.file_type_routing[0].reviewers == []


def test_load_config_file_type_routing_missing_is_empty(tmp_path: Path) -> None:
    """AC4: Missing file_type_routing defaults to empty list."""
    path = _write_yml(tmp_path, _minimal_yml())
    config = load_config(config_path=path)
    assert config.file_type_routing == []


def test_resolve_file_type_routing_matches_extension(tmp_path: Path) -> None:
    """AC4: _resolve_file_type_routing matches first rule with matching extension."""
    from revue_core.core.ai_config import FileTypeRule, resolve_file_type_routing

    rules = [
        FileTypeRule(extensions=[".yaml", ".yml"], reviewers=["docs-reviewer"]),
        FileTypeRule(extensions=[".md", ".markdown"], reviewers=["docs-reviewer"]),
    ]

    # YAML extension matches first rule
    result = resolve_file_type_routing("config.yaml", rules)
    assert result == ["docs-reviewer"]

    # YML extension also matches first rule
    result = resolve_file_type_routing("settings.yml", rules)
    assert result == ["docs-reviewer"]

    # Markdown matches second rule
    result = resolve_file_type_routing("README.md", rules)
    assert result == ["docs-reviewer"]


def test_resolve_file_type_routing_fallthrough(tmp_path: Path) -> None:
    """AC4: _resolve_file_type_routing returns None for unmatched extensions (fall-through)."""
    from revue_core.core.ai_config import FileTypeRule, resolve_file_type_routing

    rules = [
        FileTypeRule(extensions=[".yaml", ".yml"], reviewers=["docs-reviewer"]),
    ]

    # Python file doesn't match any rule
    result = resolve_file_type_routing("app.py", rules)
    assert result is None


def test_resolve_file_type_routing_empty_reviewers_means_none(tmp_path: Path) -> None:
    """AC4: empty reviewers list (matched but explicit []) returns None (fall-through signal)."""
    from revue_core.core.ai_config import FileTypeRule, resolve_file_type_routing

    rules = [
        FileTypeRule(extensions=[".tmp"], reviewers=[]),  # explicit empty = fall-through
    ]

    result = resolve_file_type_routing("file.tmp", rules)
    assert result is None


# ---------------------------------------------------------------------------
# Synthesis-model parsing — REVUE-236 follow-up (Option 3)
# ---------------------------------------------------------------------------


def test_load_config_parses_synthesis_model_from_ai_section(tmp_path: Path) -> None:
    """ai.synthesis_model is read into AIConfig.synthesis_model when present.

    Uses a real registry key so the dispatcher gate (REVUE-262) exercises the
    synthesis-model validation path instead of passing by accident.
    """
    # Arrange
    yml = """\
version: "1"
ai:
  provider: anthropic
  model: claude-haiku-4-5-20251001
  synthesis_model: claude-sonnet-4-5-20250929
"""
    path = _write_yml(tmp_path, yml)

    # Act
    config = load_config(config_path=path)

    # Assert
    assert config.synthesis_model == "claude-sonnet-4-5-20250929"
    assert config.model == "claude-haiku-4-5-20251001"


def test_load_config_synthesis_model_defaults_to_empty_when_absent(tmp_path: Path) -> None:
    """Omitting ai.synthesis_model leaves AIConfig.synthesis_model as empty string."""
    # Arrange
    path = _write_yml(tmp_path, _minimal_yml())

    # Act
    config = load_config(config_path=path)

    # Assert — empty string signals "reuse main model for synthesis"
    assert config.synthesis_model == ""


# ---------------------------------------------------------------------------
# Per-model registry integration (REVUE-262)
# ---------------------------------------------------------------------------

def test_config_loader_validates_model_registry_on_load(tmp_path: Path) -> None:
    """When `models:` is present, the dispatcher gate runs at startup.

    Selecting a built-in supported model (overridden in `models:`) must succeed.
    """
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-sonnet-4-5-20250929\n"
        "models:\n"
        "  claude-sonnet-4-5-20250929:\n"
        "    max_tokens_default: 8000\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.model == "claude-sonnet-4-5-20250929"


def test_config_loader_rejects_unknown_model_at_startup(tmp_path: Path) -> None:
    """An unknown selected model is rejected by the gate at startup.

    The gate runs against the built-in registry unconditionally — no
    ``models:`` block needed to trigger the missing-model failure.
    """
    from revue_core.core.models_registry import ModelRegistryError

    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: anthropic\n"
        "  model: totally-bogus-model\n"
    )
    path = _write_yml(tmp_path, yml)
    with pytest.raises(ModelRegistryError, match="unknown model"):
        load_config(config_path=path)


def test_config_loader_validates_against_builtin_when_no_user_overrides(
    tmp_path: Path,
) -> None:
    """m3: gate must run even without a `models:` block.

    A config selecting a built-in model with no overrides should still
    succeed — validation against the built-in registry happens unconditionally.
    """
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-sonnet-4-5-20250929\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.model == "claude-sonnet-4-5-20250929"


def test_config_loader_rejects_unknown_synthesis_model(tmp_path: Path) -> None:
    """MAJ-1: a typo in ``synthesis_model`` must fail at config-load.

    Without this, the bad model id only surfaces at Vex/Nova boot — far
    from where the config error lives.
    """
    from revue_core.core.models_registry import ModelRegistryError

    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-sonnet-4-5-20250929\n"
        "  synthesis_model: claude-banana-99\n"
    )
    path = _write_yml(tmp_path, yml)
    with pytest.raises(ModelRegistryError, match="unknown model"):
        load_config(config_path=path)


def test_config_loader_accepts_synthesis_model_equal_to_main_model(
    tmp_path: Path,
) -> None:
    """MAJ-1: omitting ``synthesis_model`` (or setting it equal to ``model``)
    must not double-validate. An empty fallback resolves at boot to the main
    model, which the gate has already cleared.
    """
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-sonnet-4-5-20250929\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    # Empty string signals "reuse main model".
    assert config.synthesis_model == ""
    assert config.model == "claude-sonnet-4-5-20250929"


# ---------------------------------------------------------------------------
# REVUE-267 follow-up: primary_language pin for reviewer agents
# ---------------------------------------------------------------------------


def test_config_loader_reads_top_level_language_into_primary_language(
    tmp_path: Path,
) -> None:
    """Top-level ``language`` in .revue.yml pins the operator's primary
    language; reviewer agents will be primed with this expertise instead of
    falling back to the language inferred from the diff."""
    yml = (
        'version: "1"\n'
        "language: swift\n"
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.primary_language == "swift"


def test_config_loader_primary_language_defaults_to_empty_when_unset(
    tmp_path: Path,
) -> None:
    """When .revue.yml omits ``language``, primary_language is empty —
    the injection step then falls back to the language inferred from the
    diff. An empty string is the contract for 'no operator pin'."""
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.primary_language == ""


# ---------------------------------------------------------------------------
# REVUE-341: Per-surface agent_timeout_seconds defaults
# ---------------------------------------------------------------------------


def test_no_yml_staging_env_ignored_applies_cli_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1 (revised): APP_ENV=staging is ignored by surface detection — cli default (600s) applies.
    APP_ENV is an internal licence-tier-override gate shared with the CI templates;
    it cannot uniquely identify /revue-local and is intentionally excluded."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("BITBUCKET_BUILD_NUMBER", raising=False)
    config = load_config(config_path=str(tmp_path / "no_such.yml"))
    assert config.agent_timeout_seconds == 600


def test_no_yml_ci_env_applies_ci_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: no .revue.yml + BITBUCKET_BUILD_NUMBER set → 600."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "99")
    config = load_config(config_path=str(tmp_path / "no_such.yml"))
    assert config.agent_timeout_seconds == 600


def test_no_yml_no_env_applies_cli_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: no .revue.yml + no surface env vars → 600 (cli default)."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("BITBUCKET_BUILD_NUMBER", raising=False)
    config = load_config(config_path=str(tmp_path / "no_such.yml"))
    assert config.agent_timeout_seconds == 600


def test_yml_explicit_timeout_overrides_surface_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4: explicit review.agent_timeout_seconds beats surface default."""
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "5")
    monkeypatch.delenv("APP_ENV", raising=False)
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
        "review:\n"
        "  agent_timeout_seconds: 300\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.agent_timeout_seconds == 300


def test_overrides_dict_beats_surface_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: overrides dict passed to load_config() beats surface default (no-yml path)."""
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "3")
    monkeypatch.delenv("APP_ENV", raising=False)
    config = load_config(
        config_path=str(tmp_path / "no_such.yml"),
        overrides={"agent_timeout_seconds": 120},
    )
    assert config.agent_timeout_seconds == 120


def test_overrides_dict_beats_surface_default_with_yml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: overrides dict beats surface default when a .revue.yml file is present."""
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "3")
    monkeypatch.delenv("APP_ENV", raising=False)
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path, overrides={"agent_timeout_seconds": 120})
    assert config.agent_timeout_seconds == 120


def test_yml_surface_defaults_overrides_built_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC8: review.surface_defaults in .revue.yml overrides built-in per-surface defaults.
    Uses BITBUCKET_BUILD_NUMBER to trigger the ci surface (300s override)."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "7")
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
        "review:\n"
        "  surface_defaults:\n"
        "    /revue-local: 900\n"
        "    ci: 300\n"
        "    cli: 180\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    assert config.agent_timeout_seconds == 300
    assert config.surface_defaults == {"/revue-local": 900, "ci": 300, "cli": 180}


def test_validate_config_rejects_surface_default_below_1(
    tmp_path: Path,
) -> None:
    """AC7: surface_defaults value of 0 is rejected by validate_config()."""
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
        "review:\n"
        "  surface_defaults:\n"
        "    cli: 0\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    errors = validate_config(config)
    assert any("surface_defaults.cli" in e for e in errors)


def test_validate_config_rejects_surface_default_above_1800(
    tmp_path: Path,
) -> None:
    """AC7: surface_defaults value of 1801 is rejected by validate_config()."""
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
        "review:\n"
        "  surface_defaults:\n"
        "    ci: 1801\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    errors = validate_config(config)
    assert any("surface_defaults.ci" in e for e in errors)


def test_validate_config_accepts_valid_surface_defaults(
    tmp_path: Path,
) -> None:
    """AC7: surface_defaults within [1, 1800] produce no errors."""
    yml = (
        'version: "1"\n'
        "ai:\n"
        "  provider: openrouter\n"
        "  model: deepseek/deepseek-v4-pro\n"
        "review:\n"
        "  surface_defaults:\n"
        "    cli: 300\n"
        "    ci: 600\n"
        "    /revue-local: 1800\n"
    )
    path = _write_yml(tmp_path, yml)
    config = load_config(config_path=path)
    errors = validate_config(config)
    surface_errors = [e for e in errors if "surface_defaults" in e]
    assert surface_errors == []


def test_default_revue_yml_contains_surface_defaults_schema() -> None:
    """AC6: DEFAULT_REVUE_YML schema block documents review.surface_defaults with all
    three canonical surface keys as commented examples."""
    from revue_core.core.config_loader import DEFAULT_REVUE_YML
    assert "surface_defaults" in DEFAULT_REVUE_YML
    assert "/revue-local" in DEFAULT_REVUE_YML
    assert "ci:" in DEFAULT_REVUE_YML
    assert "cli:" in DEFAULT_REVUE_YML

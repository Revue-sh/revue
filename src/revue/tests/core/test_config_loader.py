#!/usr/bin/env python3
"""Tests for .revue.yml config loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from revue.core.config_loader import load_config, validate_config
from revue.core.ai_config import AIConfig


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
    # Should match from_env defaults
    assert config.provider == os.getenv("REVUE_PROVIDER", "anthropic")


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
    """AC3: Project .revue.yml has exactly four allowed_patterns with correct content."""
    project_yml = Path(__file__).resolve().parents[4] / ".revue.yml"
    config = load_config(config_path=str(project_yml))
    assert len(config.allowed_patterns) == 4
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

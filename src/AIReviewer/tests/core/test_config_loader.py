#!/usr/bin/env python3
"""Tests for .revue.yml config loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from AIReviewer.core.config_loader import load_config, validate_config
from AIReviewer.core.ai_config import AIConfig


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

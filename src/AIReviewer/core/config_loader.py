#!/usr/bin/env python3
"""
.revue.yml configuration loader.

Reads the project-root YAML config, validates it, and returns a populated AIConfig.
Falls back gracefully to environment-only mode when no config file exists.

Full YAML schema::

    version: "1"                      # required, must be "1"

    ai:
      provider: anthropic             # anthropic|openai|azure|openrouter|custom
      model: claude-sonnet-4-5-20250929
      api_key_env: ANTHROPIC_API_KEY  # env var name for key (BYOK)
      base_url: ""                    # optional override
      temperature: 0.3
      max_tokens: 4096
      azure:
        endpoint: ""
        deployment: ""
        api_version: "2024-02-01"

    review:
      max_diff_lines: 2000
      min_confidence: 70
      ignore_patterns:
        - "*.md"
        - "*.lock"

    agents:
      team: team-full-review
      custom_agents_dir: ""

    output:
      format: markdown                # markdown|json|text
      file: ""
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .ai_config import AIConfig


# ---------------------------------------------------------------------------
# Default .revue.yml content (used by CLI --init in future stories)
# ---------------------------------------------------------------------------

DEFAULT_REVUE_YML: str = """# .revue.yml — Revue.io configuration
version: "1"

ai:
  provider: anthropic
  model: claude-sonnet-4-5-20250929
  api_key_env: ANTHROPIC_API_KEY

review:
  max_diff_lines: 2000
  min_confidence: 70
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "package-lock.json"
    - "*.min.js"
"""

_VALID_PROVIDERS = {"anthropic", "openai", "azure", "openrouter", "custom"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(
    config_path: str = ".revue.yml",
    overrides: dict[str, object] | None = None,
) -> AIConfig:
    """Load .revue.yml, validate, and return AIConfig.

    - If *config_path* does not exist: return ``AIConfig.from_env()`` (env-only mode).
    - Parse YAML with PyYAML.
    - Validate ``version`` field == ``"1"`` (raise ``ValueError`` otherwise).
    - Map ``ai.*`` fields onto AIConfig fields.
    - Apply *overrides* dict on top (keys match AIConfig field names).
    - Merge with env vars: env vars take precedence over .revue.yml values.
    - Return populated AIConfig.
    """
    path = Path(config_path)
    if not path.exists():
        config = AIConfig.from_env()
        if overrides:
            _apply_overrides(config, overrides)
        return config

    with open(path) as f:
        raw: dict[str, object] = yaml.safe_load(f) or {}

    # --- version gate ---
    version = raw.get("version")
    if version is None:
        raise ValueError(
            ".revue.yml: missing required field 'version'. "
            "Add  version: \"1\"  at the top of the file."
        )
    if str(version) != "1":
        raise ValueError(
            f".revue.yml: unsupported version {version!r}. Only version \"1\" is supported."
        )

    # Start from env-based defaults, then layer YAML on top
    config = AIConfig.from_env()

    # --- ai section ---
    ai: dict[str, object] = raw.get("ai", {}) or {}  # type: ignore[assignment]
    _set_if(config, "provider", ai, "provider")
    _set_if(config, "model", ai, "model")
    _set_if(config, "api_key_env", ai, "api_key_env")
    _set_if(config, "base_url", ai, "base_url")
    if "temperature" in ai:
        config.ai_temp = float(ai["temperature"])  # type: ignore[arg-type]
    if "max_tokens" in ai:
        config.ai_max_tokens = int(ai["max_tokens"])  # type: ignore[arg-type]

    azure: dict[str, object] = ai.get("azure", {}) or {}  # type: ignore[assignment]
    _set_if(config, "azure_endpoint", azure, "endpoint")
    _set_if(config, "azure_deployment", azure, "deployment")
    _set_if(config, "azure_api_version", azure, "api_version")

    # --- review section ---
    review: dict[str, object] = raw.get("review", {}) or {}  # type: ignore[assignment]
    if "max_diff_lines" in review:
        config.max_diff_lines = int(review["max_diff_lines"])  # type: ignore[arg-type]
    if "min_confidence" in review:
        config.min_confidence = int(review["min_confidence"])  # type: ignore[arg-type]
        config.ai_confidence = config.min_confidence
    if "ignore_patterns" in review:
        patterns = review["ignore_patterns"]
        config.ignore_patterns = list(patterns) if patterns else []  # type: ignore[arg-type]

    # --- agents section ---
    agents: dict[str, object] = raw.get("agents", {}) or {}  # type: ignore[assignment]
    _set_if(config, "agents_team", agents, "team")
    _set_if(config, "custom_agents_dir", agents, "custom_agents_dir")

    # --- output section ---
    output: dict[str, object] = raw.get("output", {}) or {}  # type: ignore[assignment]
    _set_if(config, "output_format", output, "format")
    _set_if(config, "output_file", output, "file")

    # --- overrides dict (CLI flags, etc.) ---
    if overrides:
        _apply_overrides(config, overrides)

    # --- env-var precedence layer ---
    _apply_env_precedence(config)

    return config


def validate_config(config: AIConfig) -> list[str]:
    """Return a list of validation error strings. Empty list means valid.

    Checks:
    - provider is one of the 5 known values
    - If provider == "azure": azure_endpoint and azure_deployment must be set
    - max_diff_lines must be > 0 and <= 10000
    - min_confidence must be 0-100
    - temperature must be 0.0-2.0
    """
    errors: list[str] = []

    if config.provider not in _VALID_PROVIDERS:
        errors.append(
            f"Unknown provider {config.provider!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_PROVIDERS))}."
        )

    if config.provider == "azure":
        if not config.azure_endpoint:
            errors.append("azure_endpoint is required when provider is 'azure'.")
        if not config.azure_deployment:
            errors.append("azure_deployment is required when provider is 'azure'.")

    if config.max_diff_lines <= 0 or config.max_diff_lines > 10000:
        errors.append(
            f"max_diff_lines must be between 1 and 10000, got {config.max_diff_lines}."
        )

    if config.min_confidence < 0 or config.min_confidence > 100:
        errors.append(
            f"min_confidence must be between 0 and 100, got {config.min_confidence}."
        )

    if config.ai_temp < 0.0 or config.ai_temp > 2.0:
        errors.append(
            f"temperature must be between 0.0 and 2.0, got {config.ai_temp}."
        )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_if(config: AIConfig, attr: str, source: dict[str, object], key: str) -> None:
    """Set *config.attr* from *source[key]* if the key is present and non-None."""
    if key in source and source[key] is not None:
        setattr(config, attr, str(source[key]))


def _apply_overrides(config: AIConfig, overrides: dict[str, object]) -> None:
    """Apply an overrides dict onto *config*, matching AIConfig field names."""
    for key, value in overrides.items():
        if hasattr(config, key):
            setattr(config, key, value)


def _apply_env_precedence(config: AIConfig) -> None:
    """Env vars take precedence over .revue.yml values for key fields."""
    env_provider = os.environ.get("REVUE_PROVIDER")
    if env_provider:
        config.provider = env_provider  # type: ignore[assignment]

    env_model = os.environ.get("REVUE_MODEL")
    if env_model:
        config.model = env_model

    env_base_url = os.environ.get("REVUE_BASE_URL")
    if env_base_url:
        config.base_url = env_base_url

    env_api_key_env = os.environ.get("REVUE_API_KEY_ENV")
    if env_api_key_env:
        config.api_key_env = env_api_key_env

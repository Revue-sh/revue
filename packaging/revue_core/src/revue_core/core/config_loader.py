#!/usr/bin/env python3
"""
.revue.yml configuration loader.

Reads the project-root YAML config, validates it, and returns a populated AIConfig.
Falls back gracefully to environment-only mode when no config file exists.

Full YAML schema::

    version: "1"                      # required, must be "1"

    ai:
      provider: openrouter            # openrouter|anthropic|openai|azure|custom
      model: deepseek/deepseek-v4-pro
      api_key_env: OPENROUTER_API_KEY # env var name for key (BYOK)
      base_url: ""                    # optional override
      temperature: 0.3
      max_tokens: 50000
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

from .ai_config import AIConfig, FileTypeRule
from .models_registry import (
    load_builtin_registry,
    merge_user_overrides,
    validate_selected_model,
)


# ---------------------------------------------------------------------------
# Default .revue.yml content (used by CLI --init in future stories)
# ---------------------------------------------------------------------------

DEFAULT_REVUE_YML: str = """# .revue.yml — Revue.io configuration
version: "1"

# Primary coding language for reviewer priming (optional).
# Operator pin — wins over the language Revue infers from the diff. Uncomment
# and set to your repository's main language (e.g. python, swift, go, ruby).
# language: python

ai:
  # Revue defaults to the cheapest reliable reviewer pair: DeepSeek-V4-Pro on
  # OpenRouter. To opt into Anthropic Sonnet instead, set:
  #   provider: anthropic
  #   model: claude-sonnet-4-5-20250929
  #   api_key_env: ANTHROPIC_API_KEY
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  api_key_env: OPENROUTER_API_KEY

review:
  max_diff_lines: 2000
  min_confidence: 70
  agent_timeout_seconds: 90  # raise to 120 for slow VPN/corporate networks
  max_parallel_agents: 1     # 1 = sequential (safe default). Raise only if your API tier has high TPM limits.
  ignore_patterns:
    - "*.md"
    - "*.lock"
    - "package-lock.json"
    - "*.min.js"

features:
  preserve_comment_threads: false  # Preserve conversation threads across commits (experimental)
  show_reviewed_files: true        # Show the list of reviewed files in the summary comment

rating:
  weights:
    high:   1.5   # penalty per HIGH finding
    medium: 0.3   # penalty per MEDIUM finding
    low:    0.05  # penalty per LOW finding
    info:   0.0   # INFO findings do not affect the score
  floor: 1.0      # minimum possible score regardless of finding count

output:
  comment_style: per-issue      # "per-issue" = one inline comment per finding (default)
                                # "summary"   = one grouped comment per file

noise_filters:
  disable: []                   # e.g. ["swift-di", "linter-suppression"]
  low_confidence_threshold: 0.5
"""

_VALID_PROVIDERS = {"anthropic", "openai", "azure", "openrouter", "custom"}

MAX_DIFF_LINES_HARD_CAP = 20000


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

    # --- top-level language pin (operator override for reviewer priming) ---
    if "language" in raw:
        lang = raw["language"]
        config.primary_language = str(lang).strip() if lang else ""

    # --- ai section ---
    ai: dict[str, object] = raw.get("ai", {}) or {}  # type: ignore[assignment]
    _set_if(config, "provider", ai, "provider")
    _set_if(config, "model", ai, "model")
    _set_if(config, "synthesis_model", ai, "synthesis_model")
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
    if "agent_timeout_seconds" in review:
        config.agent_timeout_seconds = int(review["agent_timeout_seconds"])  # type: ignore[arg-type]
    if "max_parallel_agents" in review:
        config.max_parallel_agents = int(review["max_parallel_agents"])  # type: ignore[arg-type]
    if "retry_on_rate_limit" in review:
        config.retry_on_rate_limit = bool(review["retry_on_rate_limit"])  # type: ignore[arg-type]
    if "ignore_patterns" in review:
        patterns = review["ignore_patterns"]
        config.ignore_patterns = list(patterns) if patterns else []  # type: ignore[arg-type]

    # --- noise_filters section ---
    nf: dict[str, object] = raw.get("noise_filters", {}) or {}  # type: ignore[assignment]
    if "disable" in nf:
        config.disabled_noise_filters = list(nf["disable"])  # type: ignore[arg-type]
    if "low_confidence_threshold" in nf:
        config.noise_filter_confidence_threshold = float(nf["low_confidence_threshold"])  # type: ignore[arg-type]
    if "allowed_patterns" in nf:
        config.allowed_patterns = _validate_patterns(nf["allowed_patterns"], "allowed_patterns", config_path)  # type: ignore[arg-type]
    if "disallowed_patterns" in nf:
        config.disallowed_patterns = _validate_patterns(nf["disallowed_patterns"], "disallowed_patterns", config_path)  # type: ignore[arg-type]

    # --- rating section ---
    rating: dict[str, object] = raw.get("rating", {}) or {}  # type: ignore[assignment]
    if rating:
        weights: dict[str, object] = rating.get("weights", {}) or {}  # type: ignore[assignment]
        rw = dict(config.rating_weights)
        for key in ("high", "medium", "low", "info"):
            if key in weights:
                rw[key] = float(weights[key])  # type: ignore[arg-type]
        if "floor" in rating:
            rw["floor"] = float(rating["floor"])  # type: ignore[arg-type]
        config.rating_weights = rw

    # --- consolidation section (REVUE-210 Decision 2) ---
    consolidation: dict[str, object] = raw.get("consolidation", {}) or {}  # type: ignore[assignment]
    if "proximity_lines" in consolidation:
        n = int(consolidation["proximity_lines"])  # type: ignore[arg-type]
        if n < 0:
            raise ValueError(
                f"{config_path}: consolidation.proximity_lines must be ≥ 0, got {n}"
            )
        config.consolidation_proximity_lines = n
    if "max_group_size" in consolidation:
        k = int(consolidation["max_group_size"])  # type: ignore[arg-type]
        if k < 1:
            raise ValueError(
                f"{config_path}: consolidation.max_group_size must be ≥ 1, got {k}"
            )
        config.consolidation_max_group_size = k

    # --- agents section ---
    agents: dict[str, object] = raw.get("agents", {}) or {}  # type: ignore[assignment]
    _set_if(config, "agents_team", agents, "team")
    _set_if(config, "custom_agents_dir", agents, "custom_agents_dir")

    # --- features section ---
    features: dict[str, object] = raw.get("features", {}) or {}  # type: ignore[assignment]
    if "preserve_comment_threads" in features:
        config.preserve_comment_threads = bool(features["preserve_comment_threads"])
    if "show_reviewed_files" in features:
        config.show_reviewed_files = bool(features["show_reviewed_files"])

    # --- file_type_routing section (AC4 — REVUE-166) ---
    ftr: dict[str, object] = raw.get("file_type_routing", {}) or {}  # type: ignore[assignment]
    if ftr:
        rules: list[object] = ftr.get("rules", []) or []  # type: ignore[assignment]
        if isinstance(rules, list):
            parsed_rules: list[FileTypeRule] = []
            for i, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    raise ValueError(
                        f"{config_path}: file_type_routing.rules[{i}] must be a mapping."
                    )
                extensions = rule.get("extensions", [])
                reviewers = rule.get("reviewers", [])
                if not isinstance(extensions, list):
                    raise ValueError(
                        f"{config_path}: file_type_routing.rules[{i}].extensions must be a list."
                    )
                if not isinstance(reviewers, list):
                    raise ValueError(
                        f"{config_path}: file_type_routing.rules[{i}].reviewers must be a list."
                    )
                parsed_rules.append(FileTypeRule(
                    extensions=[str(e) for e in extensions],
                    reviewers=[str(r) for r in reviewers],
                ))
            config.file_type_routing = parsed_rules

    # --- output section ---
    output: dict[str, object] = raw.get("output", {}) or {}  # type: ignore[assignment]
    _set_if(config, "output_format", output, "format")
    raw_style = output.get("comment_style")
    if raw_style is not None:
        if raw_style not in ("per-issue", "summary"):
            raise ValueError(
                f"output.comment_style must be 'per-issue' or 'summary', got {raw_style!r}."
            )
        config.comment_style = raw_style
    _set_if(config, "output_file", output, "file")

    # --- overrides dict (CLI flags, etc.) ---
    if overrides:
        _apply_overrides(config, overrides)

    # --- env-var precedence layer ---
    _apply_env_precedence(config)

    # --- per-model registry gate (REVUE-262) ---
    # Always runs against the built-in registry. User `models:` block, when
    # present, overrides per-entry knobs before validation.
    _load_and_validate_model_registry(config, raw)

    return config


def _load_and_validate_model_registry(
    ai_config: AIConfig, user_yaml: dict[str, object]
) -> None:
    """Run the per-model registry dispatcher gate at startup.

    The gate always runs against the built-in registry so that an unknown
    ``ai.model`` is rejected even when the user has no ``models:`` block.
    When a ``models:`` block is present its entries are merged on top of
    the built-in registry before validation.

    Every ``AIConfig`` field that names a model id is validated. Currently
    that is ``ai_config.model`` and ``ai_config.synthesis_model`` (Vex/Nova
    reasoning override). When ``synthesis_model`` is empty or equal to
    ``model`` it falls back to the main model — no second validation needed.
    """
    user_models = user_yaml.get("models") if isinstance(user_yaml, dict) else None
    if user_models is not None and not isinstance(user_models, dict):
        raise ValueError(
            ".revue.yml: 'models' must be a mapping of model_id -> knob mapping."
        )

    registry = load_builtin_registry()
    if user_models:
        registry = merge_user_overrides(registry, user_models)
    validate_selected_model(registry, ai_config.model)

    # Validate every other AIConfig field that names a model id. Empty string
    # (or a value identical to ``model``) means "reuse the main model" — skip.
    synthesis_model = ai_config.synthesis_model
    if synthesis_model and synthesis_model != ai_config.model:
        validate_selected_model(registry, synthesis_model)


def validate_config(config: AIConfig) -> list[str]:
    """Return a list of validation error strings. Empty list means valid.

    Checks:
    - provider is one of the 5 known values
    - If provider == "azure": azure_endpoint and azure_deployment must be set
    - max_diff_lines must be > 0 and <= MAX_DIFF_LINES_HARD_CAP
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

    if config.max_diff_lines <= 0 or config.max_diff_lines > MAX_DIFF_LINES_HARD_CAP:
        errors.append(
            f"max_diff_lines must be between 1 and {MAX_DIFF_LINES_HARD_CAP}, "
            f"got {config.max_diff_lines}."
        )

    if config.min_confidence < 0 or config.min_confidence > 100:
        errors.append(
            f"min_confidence must be between 0 and 100, got {config.min_confidence}."
        )

    if config.agent_timeout_seconds <= 0 or config.agent_timeout_seconds > 600:
        errors.append(
            f"agent_timeout_seconds must be between 1 and 600, got {config.agent_timeout_seconds}."
        )

    if config.max_parallel_agents < 1 or config.max_parallel_agents > 10:
        errors.append(
            f"max_parallel_agents must be between 1 and 10, got {config.max_parallel_agents}."
        )

    if config.ai_temp < 0.0 or config.ai_temp > 2.0:
        errors.append(
            f"temperature must be between 0.0 and 2.0, got {config.ai_temp}."
        )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_patterns(
    entries: object,
    field_name: str,
    config_path: str,
) -> list[dict[str, str]]:
    """Validate and return a list of pattern dicts from noise_filters config.

    Each entry must be a dict with 'pattern' (str) and 'rationale' (str) keys.
    Raises ValueError with a descriptive message on invalid entries.
    """
    if not entries:
        return []
    if not isinstance(entries, list):
        raise ValueError(
            f"{config_path}: noise_filters.{field_name} must be a list."
        )
    validated: list[dict[str, str]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{config_path}: noise_filters.{field_name}[{i}] must be a mapping "
                f"with 'pattern' and 'rationale' keys."
            )
        if "pattern" not in entry:
            raise ValueError(
                f"{config_path}: noise_filters.{field_name}[{i}] is missing "
                f"required 'pattern' key."
            )
        if not isinstance(entry["pattern"], str):
            raise ValueError(
                f"{config_path}: noise_filters.{field_name}[{i}].pattern must be "
                f"a string, got {type(entry['pattern']).__name__}."
            )
        if "rationale" not in entry:
            raise ValueError(
                f"{config_path}: noise_filters.{field_name}[{i}] is missing "
                f"required 'rationale' key."
            )
        if not isinstance(entry["rationale"], str):
            raise ValueError(
                f"{config_path}: noise_filters.{field_name}[{i}].rationale must be "
                f"a string, got {type(entry['rationale']).__name__}."
            )
        result: dict[str, object] = {"pattern": entry["pattern"], "rationale": entry["rationale"]}
        if "applies_to" in entry:
            at = entry["applies_to"]
            if not isinstance(at, list):
                raise ValueError(
                    f"{config_path}: noise_filters.{field_name}[{i}].applies_to must be "
                    f"a list of agent name strings, got {type(at).__name__}."
                )
            if not all(isinstance(s, str) for s in at):
                raise ValueError(
                    f"{config_path}: noise_filters.{field_name}[{i}].applies_to must "
                    f"contain only strings (agent names)."
                )
            result["applies_to"] = [s.lower().strip() for s in at]
        validated.append(result)
    return validated


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

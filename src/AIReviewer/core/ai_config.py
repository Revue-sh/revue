#!/usr/bin/env python3
"""
AI Code Reviewer Configuration.

Centralized configuration management for the AI code reviewer.
Supports multiple AI providers: Anthropic, OpenAI, Azure, OpenRouter, custom gateways.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Literal


# AI Configuration Constants
DEFAULT_AI_MAX_TOKENS = 50000
DEFAULT_AI_TEMPERATURE = 0.3
DEFAULT_AI_CONFIDENCE = 70

# Provider-default environment variable names for API keys
PROVIDER_DEFAULT_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "REVUE_API_KEY",
}


@dataclass
class AIConfig:
    """Centralized configuration for AI Code Reviewer."""

    # GitLab Configuration
    gitlab_url: str
    gitlab_token: str
    gitlab_project_id: str
    gitlab_project_path: str
    gitlab_project_url: str

    # Legacy AI Gateway Configuration (backwards compat)
    genai_gateway_url: str
    openai_api_key: str
    gen_ai_gateway_model: str
    ai_temp: float
    ai_confidence: int
    ai_max_tokens: int

    # Multi-provider Configuration
    provider: Literal["anthropic", "openai", "azure", "openrouter", "custom"] = "anthropic"
    api_key: str = ""
    api_key_env: str = ""
    base_url: str = ""
    model: str = "claude-sonnet-4-5-20250929"
    azure_endpoint: str = ""
    azure_deployment: str = ""
    azure_api_version: str = "2024-02-01"

    # Review settings (configurable via .revue.yml)
    max_diff_lines: int = 2000
    min_confidence: int = 70
    ignore_patterns: list[str] = field(default_factory=list)
    agents_team: str = "team-full-review"
    custom_agents_dir: str = ""
    output_format: str = "markdown"
    output_file: str = ""

    def __repr__(self) -> str:
        masked_key = '***' if self.api_key else ''
        return (
            f"AIConfig(provider={self.provider!r}, model={self.model!r}, "
            f"api_key=\"{masked_key}\", api_key_env={self.api_key_env!r})"
        )

    def resolve_api_key(self) -> str:
        """Resolve the API key at runtime.

        Priority order:
        1. self.api_key if set (direct value)
        2. os.environ[self.api_key_env] if api_key_env is set
        3. Provider-default env var from PROVIDER_DEFAULT_ENV_VARS
        4. Raise ValueError
        """
        if self.api_key:
            return self.api_key

        if self.api_key_env:
            value = os.environ.get(self.api_key_env)
            if value:
                return value

        default_env = PROVIDER_DEFAULT_ENV_VARS.get(self.provider)
        if default_env:
            value = os.environ.get(default_env)
            if value:
                return value

        raise ValueError(
            f"No API key found for provider {self.provider!r}. "
            f"Set api_key directly, set api_key_env to an env var name, "
            f"or export {PROVIDER_DEFAULT_ENV_VARS.get(self.provider, 'REVUE_API_KEY')}."
        )

    def validate_provider_config(self) -> List[str]:
        """Validate provider-specific configuration. Returns error messages."""
        errors: List[str] = []

        if self.provider == "azure":
            if not self.azure_endpoint:
                errors.append("azure_endpoint is required for Azure provider")
            if not self.azure_deployment:
                errors.append("azure_deployment is required for Azure provider")

        try:
            self.resolve_api_key()
        except ValueError as exc:
            errors.append(str(exc))

        return errors

    @classmethod
    def from_env(cls) -> "AIConfig":
        """Create configuration from environment variables."""
        return cls(
            # GitLab Configuration with fallbacks
            gitlab_url=os.getenv("GITLAB_URL", os.getenv("CI_SERVER_URL", "")),
            gitlab_token=os.getenv("GITLAB_TOKEN", ""),
            gitlab_project_id=os.getenv("CI_PROJECT_ID", os.getenv("GITLAB_PROJECT_ID", "")),
            gitlab_project_path=os.getenv("CI_PROJECT_PATH", os.getenv("GITLAB_PROJECT_PATH", "")),
            gitlab_project_url=os.getenv("CI_PROJECT_URL", os.getenv("GITLAB_PROJECT_URL", "")),
            # Legacy AI Gateway Configuration
            genai_gateway_url=os.getenv("GENAI_GATEWAY_URL", "https://genaigateway.ballys.tech/v1"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            gen_ai_gateway_model="claude-sonnet-4-5-20250929",
            ai_temp=DEFAULT_AI_TEMPERATURE,
            ai_confidence=DEFAULT_AI_CONFIDENCE,
            ai_max_tokens=int(os.getenv("AI_MAX_TOKENS", str(DEFAULT_AI_MAX_TOKENS))),
            # Multi-provider fields
            provider=os.getenv("REVUE_PROVIDER", "anthropic"),  # type: ignore[arg-type]
            api_key=os.getenv("OPENAI_API_KEY", ""),
            api_key_env=os.getenv("REVUE_API_KEY_ENV", ""),
            base_url=os.getenv("REVUE_BASE_URL", os.getenv("GENAI_GATEWAY_URL", "")),
            model=os.getenv("REVUE_MODEL", "claude-sonnet-4-5-20250929"),
            azure_endpoint=os.getenv("REVUE_AZURE_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT", "")),
            azure_deployment=os.getenv("REVUE_AZURE_DEPLOYMENT", os.getenv("AZURE_OPENAI_DEPLOYMENT", "")),
            azure_api_version=os.getenv("REVUE_AZURE_API_VERSION", os.getenv("AZURE_API_VERSION", "2024-02-01")),
        )

    def validate_required(self) -> List[str]:
        """Validate that all required configuration is present."""
        missing: List[str] = []
        required_fields: Dict[str, str] = {
            "gitlab_url": self.gitlab_url,
            "gitlab_token": self.gitlab_token,
            "gitlab_project_id": self.gitlab_project_id,
        }

        for field_name, value in required_fields.items():
            if not value:
                missing.append(field_name.upper())

        missing.extend(self.validate_provider_config())

        return missing


# File ignore patterns
DEFAULT_IGNORE_PATTERNS: List[str] = [
    "*.md", "*.txt", "*.json", "*.yml", "*.yaml", "*.xml",
    "*.lock", "*.log", "*.gitignore", "*.gitmodules", ".aiignore",
    "test_*", "*_test.*", "spec_*", "*_spec.*",
    "*.min.js", "*.min.css", "package-lock.json",
    "node_modules/*", "vendor/*", "third_party/*",
]

# Severity configuration
SEVERITY_CONFIG: Dict[str, Dict[str, str]] = {
    "critical": {"icon": "\U0001f534", "label": "Critical Issues"},
    "major": {"icon": "\U0001f7e1", "label": "Major Issues"},
    "minor": {"icon": "\U0001f7e2", "label": "Minor Issues"},
    "info": {"icon": "\U0001f535", "label": "Info Issues"},
    "suggestion": {"icon": "\U0001f4a1", "label": "Suggestion Issues"},
}

# Severity hierarchy for filtering
SEVERITY_HIERARCHY: Dict[str, int] = {
    "critical": 5,
    "major": 4,
    "minor": 3,
    "info": 2,
    "suggestion": 1,
}

# Common regex patterns
REGEX_PATTERNS: Dict[str, str] = {
    "word": r"\b\w+\b",
    "line_issue": r"\U0001f538 \*\*Line \d+:\*\*",
    "diff_header": r"@@ -(\d+),?\d* \+(\d+),?\d* @@",
}

# Problem categories for semantic matching
PROBLEM_CATEGORIES: Dict[str, List[str]] = {
    "memory_issues": ["retain cycle", "memory leak", "strong reference"],
    "unused_code": ["unused variable", "unused function", "unused import"],
    "force_unwrap": ["force unwrap", "force-unwrap", "nil"],
    "syntax_issues": ["syntax error", "compilation error"],
    "missing_code": ["missing", "undefined", "unresolved"],
    "accessibility": ["accessibility", "empty accessibility"],
}

# Common words to filter out from symbol extraction
COMMON_WORDS: set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "is", "are", "was", "were", "be", "been", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "can", "may", "might", "this", "that",
    "these", "those", "not", "no", "yes", "line", "code", "file", "function", "method",
    "class", "variable", "value", "error", "issue", "problem", "unused", "missing",
}

# Problem keywords for semantic fingerprinting
PROBLEM_KEYWORDS: List[str] = [
    "unused", "force-unwrap", "optional", "nil", "memory-leak", "retain-cycle",
    "strong-reference", "syntax-error", "compilation-error", "missing", "duplicate",
    "accessibility", "empty", "debugging", "placeholder", "performance",
]


def load_ignore_patterns() -> List[str]:
    """Load ignore patterns from .aiignore file, with fallback to defaults."""
    patterns: List[str] = []

    possible_paths = [
        os.path.join(os.getcwd(), ".aiignore"),
        os.path.join(os.path.dirname(os.getcwd()), ".aiignore"),
    ]

    aiignore_path: str | None = None
    for path in possible_paths:
        if os.path.exists(path):
            aiignore_path = path
            break

    if aiignore_path:
        try:
            with open(aiignore_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except Exception:
            patterns = DEFAULT_IGNORE_PATTERNS

    if not patterns:
        patterns = DEFAULT_IGNORE_PATTERNS.copy()

    return patterns


def get_config() -> AIConfig:
    """Get the global configuration instance."""
    return AIConfig.from_env()

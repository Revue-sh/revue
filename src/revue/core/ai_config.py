#!/usr/bin/env python3
"""
AI Code Reviewer Configuration.

Centralized configuration management for the AI code reviewer.
Supports multiple AI providers: Anthropic, OpenAI, Azure, OpenRouter, custom gateways.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Literal

from .key_resolver import PROVIDER_DEFAULT_ENV_VARS  # SRP: re-exported for backwards compat
from . import key_resolver as _key_resolver


# AI Configuration Constants
DEFAULT_AI_MAX_TOKENS = 50000
DEFAULT_AI_TEMPERATURE = 0.3
DEFAULT_AI_CONFIDENCE = 70


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
    agent_timeout_seconds: int = 90  # Per-agent wall-clock timeout. PRD: 90s. Raise for slow networks.
    retry_on_rate_limit: bool = False  # Retry agents with backoff on 429. Off by default (fail-fast).
    ignore_patterns: list[str] = field(default_factory=list)
    disabled_noise_filters: list[str] = field(default_factory=list)  # filter names to disable
    noise_filter_confidence_threshold: float = 0.5  # LowConfidenceFilter threshold
    allowed_patterns: list[dict[str, str]] = field(default_factory=list)  # REVUE-94
    disallowed_patterns: list[dict[str, str]] = field(default_factory=list)  # REVUE-94
    agents_team: str = "team-full-review"
    custom_agents_dir: str = ""
    output_format: str = "markdown"
    comment_style: Literal["per-issue", "summary"] = "per-issue"
    output_file: str = ""
    
    # Feature flags (configurable via .revue.yml)
    preserve_comment_threads: bool = False  # REVUE-104: preserve inline comment threads across commits

    def __repr__(self) -> str:
        masked_key = '***' if self.api_key else ''
        return (
            f"AIConfig(provider={self.provider!r}, model={self.model!r}, "
            f"api_key=\"{masked_key}\", api_key_env={self.api_key_env!r})"
        )

    # SRP: logic lives in key_resolver.py
    def resolve_api_key(self) -> str:
        """Resolve the API key at runtime (delegates to key_resolver)."""
        return _key_resolver.resolve_api_key(self)

    def validate_provider_config(self) -> List[str]:
        """Validate provider-specific configuration (delegates to key_resolver)."""
        return _key_resolver.validate_provider_config(self)

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

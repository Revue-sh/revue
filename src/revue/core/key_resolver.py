"""
API key resolution logic — separated from AIConfig data class (SRP).
"""
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_config import AIConfig

PROVIDER_DEFAULT_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "REVUE_API_KEY",
}


def resolve_api_key(config: "AIConfig") -> str:
    """
    Resolve API key for the given config. Priority:
    1. config.api_key (direct value)
    2. os.environ[config.api_key_env] if api_key_env set
    3. os.environ[PROVIDER_DEFAULT_ENV_VARS[provider]]
    4. raise ValueError with helpful message
    """
    if config.api_key:
        return config.api_key

    if config.api_key_env:
        value = os.environ.get(config.api_key_env)
        if value:
            return value

    default_env = PROVIDER_DEFAULT_ENV_VARS.get(config.provider)
    if default_env:
        value = os.environ.get(default_env)
        if value:
            return value

    raise ValueError(
        f"No API key found for provider {config.provider!r}. "
        f"Set api_key directly, set api_key_env to an env var name, "
        f"or export {PROVIDER_DEFAULT_ENV_VARS.get(config.provider, 'REVUE_API_KEY')}."
    )


def validate_provider_config(config: "AIConfig") -> list[str]:
    """Validate provider-specific fields. Returns list of error strings."""
    errors: list[str] = []

    if config.provider == "azure":
        if not config.azure_endpoint:
            errors.append("azure_endpoint is required for Azure provider")
        if not config.azure_deployment:
            errors.append("azure_deployment is required for Azure provider")

    try:
        resolve_api_key(config)
    except ValueError as exc:
        errors.append(str(exc))

    return errors

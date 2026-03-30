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


def _debug_key_resolution(config: "AIConfig") -> None:
    """Print diagnostic info when no API key is found — helps debug CI issues."""
    import sys
    print("[revue] ERROR: No API key resolved. Debug info:", file=sys.stderr, flush=True)
    print(f"[revue]   provider     = {config.provider!r}", file=sys.stderr, flush=True)
    print(f"[revue]   api_key_env  = {config.api_key_env!r}", file=sys.stderr, flush=True)

    # Check if the env var name looks unexpanded (e.g. literally "${AI_API_KEY}")
    if config.api_key_env:
        raw = config.api_key_env
        if raw.startswith("${") or raw.startswith("$"):
            print(f"[revue]   WARNING: api_key_env looks like an unexpanded shell variable: {raw!r}", file=sys.stderr, flush=True)
        val = os.environ.get(raw)
        if val is None:
            print(f"[revue]   env var {raw!r} is NOT set in the environment", file=sys.stderr, flush=True)
        elif val.startswith("${") or val.startswith("$"):
            print(f"[revue]   env var {raw!r} is set but looks unexpanded: {val!r}", file=sys.stderr, flush=True)
        else:
            masked = val[:4] + "..." if len(val) > 4 else "****"
            print(f"[revue]   env var {raw!r} is set, value starts with: {masked}", file=sys.stderr, flush=True)

    default_env = PROVIDER_DEFAULT_ENV_VARS.get(config.provider, "")
    if default_env:
        val = os.environ.get(default_env)
        if val is None:
            print(f"[revue]   default env var {default_env!r} is NOT set", file=sys.stderr, flush=True)
        else:
            masked = val[:4] + "..." if len(val) > 4 else "****"
            print(f"[revue]   default env var {default_env!r} starts with: {masked}", file=sys.stderr, flush=True)


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

    # At this point no key was found — log what we actually received for debugging
    _debug_key_resolution(config)

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

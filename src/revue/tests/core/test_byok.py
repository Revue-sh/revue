#!/usr/bin/env python3
"""Tests for BYOK (Bring Your Own Key) env var resolution."""

from __future__ import annotations

import os
from typing import Any

import pytest

from revue.core.ai_config import AIConfig, PROVIDER_DEFAULT_ENV_VARS


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_config(**overrides: Any) -> AIConfig:
    """Return an AIConfig with sensible test defaults, overridden by *overrides*."""
    defaults: dict[str, Any] = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="glpat-test",
        gitlab_project_id="42",
        gitlab_project_path="org/repo",
        gitlab_project_url="https://gitlab.example.com/org/repo",
        genai_gateway_url="https://gateway.example.com/v1",
        openai_api_key="sk-test",
        gen_ai_gateway_model="claude-sonnet-4-5-20250929",
        ai_temp=0.3,
        ai_confidence=70,
        ai_max_tokens=50000,
        provider="anthropic",
        api_key="",
        api_key_env="",
        base_url="",
        model="claude-sonnet-4-5-20250929",
        azure_endpoint="",
        azure_deployment="",
        azure_api_version="2024-02-01",
    )
    defaults.update(overrides)
    return AIConfig(**defaults)


# ---------------------------------------------------------------------------
# resolve_api_key tests (1-8)
# ---------------------------------------------------------------------------

def test_resolve_api_key_direct() -> None:
    """api_key set directly takes highest priority."""
    config = _make_config(api_key="sk-direct-key")
    assert config.resolve_api_key() == "sk-direct-key"


def test_resolve_api_key_from_named_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_key_env points to a custom env var name."""
    monkeypatch.setenv("MY_KEY", "abc")
    config = _make_config(api_key_env="MY_KEY")
    assert config.resolve_api_key() == "abc"


def test_resolve_api_key_provider_default_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to ANTHROPIC_API_KEY for anthropic provider."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-123")
    config = _make_config(provider="anthropic")
    assert config.resolve_api_key() == "ant-key-123"


def test_resolve_api_key_provider_default_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to OPENAI_API_KEY for openai provider."""
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key-456")
    config = _make_config(provider="openai")
    assert config.resolve_api_key() == "oai-key-456"


def test_resolve_api_key_provider_default_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to AZURE_OPENAI_API_KEY for azure provider."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key-789")
    config = _make_config(provider="azure")
    assert config.resolve_api_key() == "azure-key-789"


def test_resolve_api_key_provider_default_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to OPENROUTER_API_KEY for openrouter provider."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-abc")
    config = _make_config(provider="openrouter")
    assert config.resolve_api_key() == "or-key-abc"


def test_resolve_api_key_provider_default_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to REVUE_API_KEY for custom provider."""
    monkeypatch.setenv("REVUE_API_KEY", "custom-key-def")
    config = _make_config(provider="custom")
    assert config.resolve_api_key() == "custom-key-def"


def test_resolve_api_key_raises_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raises ValueError with helpful message when no key is found anywhere."""
    # Ensure provider-default env var is not set
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = _make_config(provider="anthropic")
    with pytest.raises(ValueError, match=r"No API key found for provider 'anthropic'"):
        config.resolve_api_key()


# ---------------------------------------------------------------------------
# validate_provider_config tests (9-10)
# ---------------------------------------------------------------------------

def test_validate_provider_config_azure_missing_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Azure provider without azure_endpoint returns errors."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    config = _make_config(provider="azure", azure_endpoint="", azure_deployment="")
    errors = config.validate_provider_config()
    assert any("azure_endpoint" in e for e in errors)
    assert any("azure_deployment" in e for e in errors)


def test_validate_provider_config_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic provider with key set returns no errors."""
    config = _make_config(provider="anthropic", api_key="sk-valid")
    errors = config.validate_provider_config()
    assert errors == []


# ---------------------------------------------------------------------------
# __repr__ security test
# ---------------------------------------------------------------------------

def test_repr_masks_api_key() -> None:
    """__repr__ must never expose the raw api_key value."""
    config = _make_config(api_key="sk-super-secret-key")
    r = repr(config)
    assert "sk-super-secret-key" not in r
    assert '***' in r


def test_repr_empty_key() -> None:
    """__repr__ shows empty string when api_key is not set."""
    config = _make_config(api_key="")
    r = repr(config)
    assert 'api_key=""' in r

#!/usr/bin/env python3
"""REVUE-267: pin DeepSeek-on-OpenRouter as the default model + provider.

These tests guard the cost-driven swap of the implicit default. Customers
who explicitly set ``REVUE_PROVIDER`` or ``REVUE_MODEL`` (or a ``.revue.yml``
``ai.model``) are unaffected — only the implicit-default codepath moves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from revue.core.ai_config import AIConfig
from revue.core.config_loader import load_config


_DEEPSEEK_MODEL = "deepseek/deepseek-v4-pro"
_SONNET_MODEL = "claude-sonnet-4-5-20250929"


# ---------------------------------------------------------------------------
# AC1 / AC2 — dataclass-level defaults
# ---------------------------------------------------------------------------


def _clear_revue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every env var that AIConfig.from_env reads as a default override."""
    for name in (
        "REVUE_PROVIDER",
        "REVUE_MODEL",
        "REVUE_API_KEY_ENV",
        "REVUE_BASE_URL",
        "REVUE_AZURE_ENDPOINT",
        "REVUE_AZURE_DEPLOYMENT",
        "REVUE_AZURE_API_VERSION",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_ai_config_dataclass_default_model_is_deepseek_v4_pro() -> None:
    # Arrange + Act — dataclass default with all required kwargs satisfied
    config = AIConfig(
        gitlab_url="",
        gitlab_token="",
        gitlab_project_id="",
        gitlab_project_path="",
        gitlab_project_url="",
        genai_gateway_url="",
        openai_api_key="",
        gen_ai_gateway_model="",
        ai_temp=0.0,
        ai_confidence=70,
        ai_max_tokens=2048,
    )

    # Assert — DeepSeek-V4-Pro is the implicit default model
    assert config.model == _DEEPSEEK_MODEL


def test_ai_config_dataclass_default_provider_is_openrouter() -> None:
    # Arrange + Act — dataclass default with all required kwargs satisfied
    config = AIConfig(
        gitlab_url="",
        gitlab_token="",
        gitlab_project_id="",
        gitlab_project_path="",
        gitlab_project_url="",
        genai_gateway_url="",
        openai_api_key="",
        gen_ai_gateway_model="",
        ai_temp=0.0,
        ai_confidence=70,
        ai_max_tokens=2048,
    )

    # Assert — OpenRouter is the implicit default provider
    assert config.provider == "openrouter"


# ---------------------------------------------------------------------------
# AC2 — from_env() with no overrides
# ---------------------------------------------------------------------------


def test_from_env_with_no_revue_vars_resolves_to_deepseek_and_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — strip every REVUE_* override so we see the real defaults
    _clear_revue_env(monkeypatch)

    # Act
    config = AIConfig.from_env()

    # Assert — provider and model both default to the OpenRouter/DeepSeek pair
    assert config.provider == "openrouter"
    assert config.model == _DEEPSEEK_MODEL


def test_from_env_with_explicit_anthropic_override_preserves_sonnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — customer who pinned Anthropic explicitly must keep working
    _clear_revue_env(monkeypatch)
    monkeypatch.setenv("REVUE_PROVIDER", "anthropic")
    monkeypatch.setenv("REVUE_MODEL", _SONNET_MODEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    # Act
    config = AIConfig.from_env()

    # Assert — explicit override beats the new defaults
    assert config.provider == "anthropic"
    assert config.model == _SONNET_MODEL


# ---------------------------------------------------------------------------
# AC3 — dispatcher gate accepts the new default
# ---------------------------------------------------------------------------


def test_load_config_with_no_revue_yml_passes_dispatcher_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — no .revue.yml, no REVUE_MODEL/PROVIDER overrides
    _clear_revue_env(monkeypatch)
    nonexistent = str(tmp_path / "no_such_file.yml")

    # Act — should not raise (the new default is a supported registry entry)
    config = load_config(config_path=nonexistent)

    # Assert — the gate accepted the implicit default, and the registry pair holds
    assert config.provider == "openrouter"
    assert config.model == _DEEPSEEK_MODEL


# ---------------------------------------------------------------------------
# AC4 — legacy Ballys gateway field is intentionally untouched
# ---------------------------------------------------------------------------


def test_from_env_keeps_legacy_gen_ai_gateway_model_pinned_to_sonnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange — the legacy gateway path is a separate concern from the
    # multi-provider default. AC4 forbids changing it under this ticket.
    _clear_revue_env(monkeypatch)

    # Act
    config = AIConfig.from_env()

    # Assert — gen_ai_gateway_model stays on Sonnet (legacy gateway), even
    # though the multi-provider default has moved to DeepSeek/OpenRouter.
    assert config.gen_ai_gateway_model == _SONNET_MODEL


# ---------------------------------------------------------------------------
# AC5 — no implicit-default Sonnet pin remains in non-test source
# ---------------------------------------------------------------------------


def test_no_implicit_sonnet_default_in_ai_config_module() -> None:
    """``ai_config.py`` must not pin Sonnet as the multi-provider default.

    After REVUE-267 the only remaining ``claude-sonnet-4-5-20250929`` literal
    in ``ai_config.py`` is the legacy ``gen_ai_gateway_model`` (AC4 carve-out).
    Any other occurrence is a regression — likely a forgotten implicit-default
    hardcode.
    """
    # Arrange — read the production module verbatim
    module_path = Path(__file__).resolve().parents[2] / "core" / "ai_config.py"
    source = module_path.read_text()

    # Act — find every line that mentions the Sonnet model id
    sonnet_lines = [
        line for line in source.splitlines() if _SONNET_MODEL in line
    ]

    # Assert — exactly one line remains, and it's the legacy gateway field (AC4)
    assert len(sonnet_lines) == 1, (
        f"Expected exactly one Sonnet reference in ai_config.py "
        f"(the AC4 gen_ai_gateway_model line); found {len(sonnet_lines)}:\n"
        + "\n".join(sonnet_lines)
    )
    assert "gen_ai_gateway_model" in sonnet_lines[0], (
        f"Remaining Sonnet reference is not the AC4 gen_ai_gateway_model line: "
        f"{sonnet_lines[0]!r}"
    )


# ---------------------------------------------------------------------------
# AC7 / regression — ai_config tests do not depend on Sonnet being the default
# ---------------------------------------------------------------------------


def test_default_keyword_is_documented_for_synthesis_model_fallback() -> None:
    """The synthesis_model fallback semantics ("empty -> reuse main model")
    must keep working when the main model is DeepSeek, not Sonnet.

    This is a contract test: instantiate with the new default and the empty
    synthesis_model, and assert the same "reuse main model" behaviour the
    existing REVUE-236/240 contract relies on.
    """
    # Arrange — implicit defaults, empty synthesis_model
    config = AIConfig(
        gitlab_url="",
        gitlab_token="",
        gitlab_project_id="",
        gitlab_project_path="",
        gitlab_project_url="",
        genai_gateway_url="",
        openai_api_key="",
        gen_ai_gateway_model="",
        ai_temp=0.0,
        ai_confidence=70,
        ai_max_tokens=2048,
    )

    # Assert — synthesis_model still signals "reuse main", and main is DeepSeek
    assert config.synthesis_model == ""
    assert config.model == _DEEPSEEK_MODEL


# ---------------------------------------------------------------------------
# Sanity guard — the model id we swap to must match the registry exactly
# ---------------------------------------------------------------------------


def test_deepseek_default_matches_registry_entry_exactly() -> None:
    """Cross-check: the new default literal must be the exact key in
    ``models_registry.yml``. A typo here would silently break startup
    everywhere the dispatcher gate runs.
    """
    # Arrange — read the built-in registry from source
    registry_path = (
        Path(__file__).resolve().parents[2] / "core" / "models_registry.yml"
    )
    registry_text = registry_path.read_text()

    # Act — look for the model id as a top-level key under `models:`
    pattern = re.compile(rf"^\s{{2}}{re.escape(_DEEPSEEK_MODEL)}:\s*$", re.MULTILINE)

    # Assert — the registry has an entry whose key matches our default literal
    assert pattern.search(registry_text), (
        f"models_registry.yml has no top-level entry for {_DEEPSEEK_MODEL!r}; "
        f"the default-swap would fail the dispatcher gate at startup."
    )

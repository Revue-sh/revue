#!/usr/bin/env python3
"""Tests for the per-model registry + dispatcher gate (REVUE-262).

Covers:
- Loading the built-in registry from `models_registry.yml`.
- Forward-compat: unknown knob keys are tolerated and dropped into `extras`.
- Pure-function merge of user overrides (no mutation; per-entry overrides;
  customer-added entries default to tier=unsupported).
- The dispatcher gate `validate_selected_model` (missing model, supported
  without schema_strict, customer-added unsupported pass-through).
- `ModelConfig` immutability.
"""

from __future__ import annotations

import dataclasses

import pytest

from revue_core.core.models_registry import (
    ModelConfig,
    ModelRegistryError,
    load_builtin_registry,
    merge_user_overrides,
    validate_selected_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    model_id: str = "test-model",
    *,
    provider: str = "anthropic",
    schema_mode: str = "output_config",
    schema_strict: bool = True,
    tool_choice_first_turn: str = "auto",
    max_tokens_default: int = 1024,
    tier: str = "supported",
    extras: dict[str, object] | None = None,
) -> ModelConfig:
    return ModelConfig(
        model_id=model_id,
        provider=provider,
        schema_mode=schema_mode,
        schema_strict=schema_strict,
        tool_choice_first_turn=tool_choice_first_turn,
        max_tokens_default=max_tokens_default,
        tier=tier,
        extras=dict(extras) if extras else {},
    )


# ---------------------------------------------------------------------------
# Built-in registry loading
# ---------------------------------------------------------------------------

def test_load_builtin_registry_returns_supported_entries() -> None:
    registry = load_builtin_registry()

    assert set(registry.keys()) == {
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
        "qwen/qwen3-coder-next",
        "deepseek/deepseek-v4-pro",
    }
    for cfg in registry.values():
        assert cfg.tier == "supported"
        assert cfg.schema_strict is True

    sonnet = registry["claude-sonnet-4-5-20250929"]
    assert sonnet.provider == "anthropic"
    assert sonnet.schema_mode == "output_config"
    assert sonnet.schema_strict is True
    assert sonnet.tool_choice_first_turn == "auto"
    assert sonnet.max_tokens_default == 4096
    assert sonnet.tier == "supported"

    haiku = registry["claude-haiku-4-5-20251001"]
    assert haiku.provider == "anthropic"
    assert haiku.max_tokens_default == 2048
    assert haiku.tier == "supported"

    qwen = registry["qwen/qwen3-coder-next"]
    assert qwen.provider == "openrouter"
    assert qwen.schema_mode == "response_format"
    assert qwen.tool_choice_first_turn == "required"
    assert qwen.max_tokens_default == 2048
    assert qwen.tier == "supported"


def test_load_builtin_registry_tolerates_unknown_knob_keys(
    tmp_path, monkeypatch
) -> None:
    """Unknown keys must not crash the loader — they land in `extras`."""
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  fake-model:\n"
        "    provider: anthropic\n"
        "    schema_mode: output_config\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n"
        "    tier: supported\n"
        "    future_knob_x: 42\n"
        "    future_knob_y: hello\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    registry = load_builtin_registry()
    cfg = registry["fake-model"]
    assert cfg.extras == {"future_knob_x": 42, "future_knob_y": "hello"}


# ---------------------------------------------------------------------------
# merge_user_overrides
# ---------------------------------------------------------------------------

def test_merge_user_overrides_per_entry_override_without_mutation() -> None:
    builtin = {"a": _make_config("a", max_tokens_default=1024)}
    user = {"a": {"max_tokens_default": 8000}}

    merged = merge_user_overrides(builtin, user)

    # Original untouched
    assert builtin["a"].max_tokens_default == 1024
    # Merged reflects override
    assert merged["a"].max_tokens_default == 8000
    # Other fields preserved
    assert merged["a"].provider == "anthropic"
    assert merged["a"].schema_strict is True


def test_merge_user_overrides_adds_customer_entry_with_unsupported_default() -> None:
    builtin: dict[str, ModelConfig] = {}
    user = {
        "deepseek/custom": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": True,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 2048,
        }
    }

    merged = merge_user_overrides(builtin, user)

    assert "deepseek/custom" in merged
    assert merged["deepseek/custom"].tier == "unsupported"


def test_merge_user_overrides_respects_explicit_tier_on_customer_entry() -> None:
    """Customer-added entries default to unsupported, but explicit tier wins."""
    builtin: dict[str, ModelConfig] = {}
    user = {
        "my/model": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": True,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 2048,
            "tier": "supported",
        }
    }

    merged = merge_user_overrides(builtin, user)
    assert merged["my/model"].tier == "supported"


# ---------------------------------------------------------------------------
# validate_selected_model — the dispatcher gate
# ---------------------------------------------------------------------------

def test_validate_selected_model_returns_config_on_supported_entry() -> None:
    registry = {"good": _make_config("good")}
    cfg = validate_selected_model(registry, "good")
    assert cfg.model_id == "good"


def test_validate_selected_model_rejects_missing_model_id() -> None:
    registry = {"good": _make_config("good")}
    with pytest.raises(ModelRegistryError, match="unknown model"):
        validate_selected_model(registry, "nope")


def test_validate_selected_model_rejects_supported_without_schema_strict() -> None:
    registry = {"bad": _make_config("bad", schema_strict=False, tier="supported")}
    with pytest.raises(ModelRegistryError, match="schema_strict"):
        validate_selected_model(registry, "bad")


def test_validate_selected_model_accepts_unsupported_with_explicit_tier() -> None:
    registry = {
        "weak": _make_config("weak", schema_strict=False, tier="unsupported")
    }
    cfg = validate_selected_model(registry, "weak")
    assert cfg.tier == "unsupported"


def test_validate_selected_model_accepts_customer_added_unsupported() -> None:
    """A customer entry merged in (default unsupported) passes silently."""
    builtin: dict[str, ModelConfig] = {}
    user = {
        "my/custom": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": False,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 1024,
        }
    }
    merged = merge_user_overrides(builtin, user)
    cfg = validate_selected_model(merged, "my/custom")
    assert cfg.tier == "unsupported"


# ---------------------------------------------------------------------------
# ModelConfig immutability
# ---------------------------------------------------------------------------

def test_modelconfig_is_frozen() -> None:
    cfg = _make_config("frozen")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.max_tokens_default = 9999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Post-bmad-code-review regression tests (REVUE-262)
# ---------------------------------------------------------------------------

def test_validate_selected_model_accepts_anthropic_optin_model_id() -> None:
    """B1: the built-in registry must keep ``claude-sonnet-4-5-20250929`` as
    a supported, strict-schema entry so REVUE-267's Anthropic opt-in path
    (AC4) remains valid after the default flipped to deepseek-v4-pro on
    OpenRouter.
    """
    registry = load_builtin_registry()
    cfg = validate_selected_model(registry, "claude-sonnet-4-5-20250929")
    assert cfg.model_id == "claude-sonnet-4-5-20250929"
    assert cfg.tier == "supported"
    assert cfg.schema_strict is True


def test_load_rejects_non_bool_schema_strict(tmp_path, monkeypatch) -> None:
    """M1: ``schema_strict: "true"`` (string) must not slip past the gate.

    The legacy ``bool(pick(...))`` form returned True for any non-empty
    string, so string values bypassed the strictness contract.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  bad-model:\n"
        "    provider: anthropic\n"
        "    schema_mode: output_config\n"
        '    schema_strict: "true"\n'  # <-- string, not bool
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n"
        "    tier: supported\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    with pytest.raises(ModelRegistryError, match="schema_strict must be a boolean"):
        load_builtin_registry()


def test_tier_case_insensitive_or_strictly_validated(
    tmp_path, monkeypatch
) -> None:
    """M2: ``tier: Supported`` must not bypass the gate via case mismatch.

    We normalise ``tier`` to lower-case at parse time so the gate's
    ``cfg.tier == "supported"`` comparison catches mixed-case entries.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  case-model:\n"
        "    provider: anthropic\n"
        "    schema_mode: output_config\n"
        "    schema_strict: false\n"
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n"
        "    tier: Supported\n"  # <-- mixed case
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    registry = load_builtin_registry()
    # Normalisation: stored tier is lowercase.
    assert registry["case-model"].tier == "supported"
    # And the gate now catches it (supported + schema_strict=False).
    with pytest.raises(ModelRegistryError, match="schema_strict"):
        validate_selected_model(registry, "case-model")


def test_load_rejects_unknown_tier_value(tmp_path, monkeypatch) -> None:
    """M3: ``tier`` is a closed enum; unknown values must raise at parse time."""
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  bad-tier:\n"
        "    provider: anthropic\n"
        "    schema_mode: output_config\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n"
        "    tier: experimental\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    with pytest.raises(ModelRegistryError, match="tier must be one of"):
        load_builtin_registry()


def test_validate_rejects_customer_override_disabling_schema_strict_on_supported_entry() -> None:
    """M4: a user override cannot weaken a supported built-in entry.

    The gate must fire even though the strictness loss came from a merged
    override rather than the built-in YAML.
    """
    builtin = {
        "sonnet": _make_config(
            "sonnet", tier="supported", schema_strict=True
        )
    }
    user = {"sonnet": {"schema_strict": False}}

    merged = merge_user_overrides(builtin, user)

    with pytest.raises(ModelRegistryError, match="schema_strict"):
        validate_selected_model(merged, "sonnet")


def test_extras_dict_is_read_only() -> None:
    """m1: ``ModelConfig.extras`` is wrapped in MappingProxyType.

    A frozen dataclass with a mutable dict field leaks the immutability
    guarantee; the wrap closes the hole.
    """
    cfg = _make_config("immut", extras={"future_knob": 1})
    with pytest.raises(TypeError):
        cfg.extras["future_knob"] = 99  # type: ignore[index]


def test_modelconfig_deepcopy_preserves_extras_read_only() -> None:
    """MIN-1: ``copy.deepcopy(cfg)`` must round-trip and keep extras read-only.

    ``MappingProxyType`` is not robustly deepcopyable; without explicit
    ``__getstate__``/``__setstate__`` hooks this is one CPython patch away
    from a ``TypeError``. The clone must still reject writes.
    """
    import copy

    cfg = _make_config("immut", extras={"future_knob": 1})
    clone = copy.deepcopy(cfg)

    assert clone.model_id == cfg.model_id
    assert dict(clone.extras) == {"future_knob": 1}
    # Read-only contract survives the copy.
    with pytest.raises(TypeError):
        clone.extras["future_knob"] = 99  # type: ignore[index]
    # Clone's extras dict is independent of the original.
    assert clone.extras is not cfg.extras


def test_load_rejects_non_string_tier(tmp_path, monkeypatch) -> None:
    """MIN-2: ``tier`` must be a string before lowercase coercion.

    Symmetry with ``schema_strict`` — a YAML scalar like ``tier: 1`` is a
    user mistake, not a value to coerce to ``"1"`` and then reject with a
    misleading message.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  bad-tier:\n"
        "    provider: anthropic\n"
        "    schema_mode: output_config\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n"
        "    tier: 1\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    with pytest.raises(ModelRegistryError, match="tier must be a string"):
        load_builtin_registry()


def test_load_builtin_registry_wraps_io_errors(tmp_path, monkeypatch) -> None:
    """m2: missing/broken built-in registry surfaces as ModelRegistryError.

    A low-level ``FileNotFoundError`` traceback would obscure the real
    install-shape regression at startup; the wrap gives a clean error.
    """
    from revue_core.core import models_registry as mod

    missing = tmp_path / "does-not-exist.yml"
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", missing)

    with pytest.raises(ModelRegistryError, match="failed to load built-in registry"):
        load_builtin_registry()

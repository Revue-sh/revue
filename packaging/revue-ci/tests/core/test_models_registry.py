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
    reasoning_assembler: str | None = None,
    reasoning_mode: str = "none",
    reasoning_param: dict[str, object] | None = None,
    schema_mode_when_reasoning: str | None = None,
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
        reasoning_assembler=reasoning_assembler,
        reasoning_mode=reasoning_mode,
        reasoning_param=reasoning_param,
        schema_mode_when_reasoning=schema_mode_when_reasoning,
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


# ---------------------------------------------------------------------------
# REVUE-324 — Reasoning channel knobs (Vex Option C)
# ---------------------------------------------------------------------------

def test_deepseek_builtin_entry_has_reasoning_knobs() -> None:
    """REVUE-324 TC1a: built-in DeepSeek entry declares the reasoning-channel
    knobs. Qwen/Sonnet/Haiku entries MUST NOT declare them (Daniel, scope
    discipline: blast-radius-safe by default).
    """
    registry = load_builtin_registry()
    ds = registry["deepseek/deepseek-v4-pro"]

    assert ds.reasoning_assembler == "deepseek_v4"
    assert ds.reasoning_mode == "separate_channel"
    assert ds.reasoning_param == {"enabled": True, "effort": "high"}
    assert ds.schema_mode_when_reasoning == "json_object"

    # Other built-in entries: no assembler set — bit-identical wire shape.
    for other in (
        "qwen/qwen3-coder-next",
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
    ):
        cfg = registry[other]
        assert cfg.reasoning_assembler is None
        assert cfg.reasoning_mode == "none"
        assert cfg.reasoning_param is None
        assert cfg.schema_mode_when_reasoning is None


def test_modelconfig_reasoning_field_defaults() -> None:
    """REVUE-324 TC1b: ModelConfig reasoning fields default to no-op."""
    cfg = _make_config("any")
    assert cfg.reasoning_assembler is None
    assert cfg.reasoning_mode == "none"
    assert cfg.reasoning_param is None
    assert cfg.schema_mode_when_reasoning is None


def test_load_parses_reasoning_knobs(tmp_path, monkeypatch) -> None:
    """REVUE-324 TC1: loader parses the four reasoning knobs into typed fields,
    not into ``extras``.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  reason-model:\n"
        "    provider: openrouter\n"
        "    schema_mode: response_format\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: required\n"
        "    max_tokens_default: 2048\n"
        "    tier: supported\n"
        "    reasoning_assembler: deepseek_v4\n"
        "    reasoning_mode: separate_channel\n"
        "    reasoning_param:\n"
        "      enabled: true\n"
        "      effort: high\n"
        "    schema_mode_when_reasoning: json_object\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    cfg = load_builtin_registry()["reason-model"]
    assert cfg.reasoning_assembler == "deepseek_v4"
    assert cfg.reasoning_mode == "separate_channel"
    assert cfg.reasoning_param == {"enabled": True, "effort": "high"}
    assert cfg.schema_mode_when_reasoning == "json_object"
    # Reasoning knobs are KNOWN keys — must NOT land in extras.
    assert "reasoning_assembler" not in cfg.extras
    assert "reasoning_mode" not in cfg.extras
    assert "reasoning_param" not in cfg.extras
    assert "schema_mode_when_reasoning" not in cfg.extras


@pytest.mark.parametrize(
    "bad_mode",
    ["inline", "anthropic_thinking", "chain_of_thought", "invalid", ""],
)
def test_load_rejects_unknown_reasoning_mode(
    bad_mode, tmp_path, monkeypatch
) -> None:
    """REVUE-324 TC4: ``reasoning_mode`` is a closed enum.

    Today only ``none`` and ``separate_channel`` are valid. ``inline`` and
    ``anthropic_thinking`` are deferred to follow-up tickets; accepting them
    here would silently no-op until a corresponding assembler is wired,
    which would be worse than failing fast.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  bad-reason:\n"
        "    provider: openrouter\n"
        "    schema_mode: response_format\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: required\n"
        "    max_tokens_default: 2048\n"
        "    tier: supported\n"
        f"    reasoning_mode: {bad_mode!r}\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    with pytest.raises(ModelRegistryError, match="reasoning_mode must be one of"):
        load_builtin_registry()


def test_load_rejects_unknown_reasoning_assembler(
    tmp_path, monkeypatch
) -> None:
    """REVUE-324 TC3: ``reasoning_assembler`` set to a name not in the known
    set must fail at parse time. Error message names available assemblers.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  typo-assembler:\n"
        "    provider: openrouter\n"
        "    schema_mode: response_format\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: required\n"
        "    max_tokens_default: 2048\n"
        "    tier: supported\n"
        "    reasoning_assembler: bogus\n"
        "    reasoning_mode: separate_channel\n"
        "    schema_mode_when_reasoning: json_object\n"
        "    reasoning_param:\n"
        "      enabled: true\n"
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)

    with pytest.raises(
        ModelRegistryError, match="reasoning_assembler.*Known assemblers"
    ):
        load_builtin_registry()


def test_validate_selected_model_rejects_separate_channel_without_schema_mode() -> None:
    """REVUE-324 TC2a: ``reasoning_mode=separate_channel`` co-requires
    ``schema_mode_when_reasoning``, ``reasoning_assembler``, and
    ``reasoning_param``. Missing schema mode trips the gate.
    """
    cfg = _make_config(
        "incomplete",
        provider="openrouter",  # bypass the provider-capability gate
        reasoning_mode="separate_channel",
        reasoning_assembler="deepseek_v4",
        reasoning_param={"enabled": True},
        schema_mode_when_reasoning=None,
    )
    registry = {"incomplete": cfg}

    with pytest.raises(
        ModelRegistryError, match="schema_mode_when_reasoning"
    ):
        validate_selected_model(registry, "incomplete")


def test_validate_selected_model_rejects_separate_channel_without_assembler() -> None:
    """REVUE-324 TC2b: co-requirement — missing assembler trips the gate."""
    cfg = _make_config(
        "incomplete",
        provider="openrouter",  # bypass the provider-capability gate
        reasoning_mode="separate_channel",
        reasoning_assembler=None,
        reasoning_param={"enabled": True},
        schema_mode_when_reasoning="json_object",
    )
    registry = {"incomplete": cfg}

    with pytest.raises(
        ModelRegistryError, match="reasoning_assembler"
    ):
        validate_selected_model(registry, "incomplete")


def test_validate_selected_model_accepts_reasoning_mode_none_with_no_extras() -> None:
    """REVUE-324: default state (``reasoning_mode=none`` + everything ``None``)
    passes the gate cleanly — this is today's behaviour for every
    non-DeepSeek model and must not regress.
    """
    cfg = _make_config(
        "neutral",
        reasoning_mode="none",
        reasoning_assembler=None,
        reasoning_param=None,
        schema_mode_when_reasoning=None,
    )
    registry = {"neutral": cfg}
    resolved = validate_selected_model(registry, "neutral")
    assert resolved.reasoning_mode == "none"


def test_validate_selected_model_accepts_full_deepseek_triple() -> None:
    """REVUE-324: the full DeepSeek shape passes the gate."""
    cfg = _make_config(
        "deepseek/deepseek-v4-pro",
        provider="openrouter",
        schema_mode="response_format",
        reasoning_mode="separate_channel",
        reasoning_assembler="deepseek_v4",
        reasoning_param={"enabled": True, "effort": "high"},
        schema_mode_when_reasoning="json_object",
    )
    registry = {cfg.model_id: cfg}
    resolved = validate_selected_model(registry, cfg.model_id)
    assert resolved.reasoning_assembler == "deepseek_v4"


def test_customer_can_reuse_deepseek_assembler_on_extended_entry() -> None:
    """REVUE-324 TC16: customer adds a derived DeepSeek model under their
    own id and reuses ``deepseek_v4``. The assembler-name lookup honours it.
    """
    builtin: dict[str, ModelConfig] = {}
    user = {
        "deepseek/deepseek-v5-pro": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": True,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 2048,
            "reasoning_assembler": "deepseek_v4",
            "reasoning_mode": "separate_channel",
            "reasoning_param": {"enabled": True, "effort": "high"},
            "schema_mode_when_reasoning": "json_object",
        }
    }
    merged = merge_user_overrides(builtin, user)
    cfg = validate_selected_model(merged, "deepseek/deepseek-v5-pro")
    assert cfg.reasoning_assembler == "deepseek_v4"
    assert cfg.tier == "unsupported"  # customer-added default


# ---------------------------------------------------------------------------
# REVUE-324 code-review cycle 1 patches
# ---------------------------------------------------------------------------


def test_validate_selected_model_rejects_separate_channel_without_reasoning_param() -> None:
    """REVUE-324 cycle 1 patch: ``reasoning_param`` is part of the
    co-requirement set alongside ``reasoning_assembler`` and
    ``schema_mode_when_reasoning``. A missing payload would emit
    ``{"reasoning": {}}`` on the wire — provider may reject or silently
    disable thinking. Fail fast at startup.
    """
    cfg = _make_config(
        "deepseek/incomplete",
        provider="openrouter",
        reasoning_mode="separate_channel",
        reasoning_assembler="deepseek_v4",
        reasoning_param=None,  # <-- missing
        schema_mode_when_reasoning="json_object",
    )
    registry = {cfg.model_id: cfg}
    with pytest.raises(ModelRegistryError, match="reasoning_param is not set"):
        validate_selected_model(registry, cfg.model_id)


def test_validate_selected_model_rejects_separate_channel_on_non_openrouter_provider() -> None:
    """REVUE-324 cycle 1 patch: only providers that wire ``reasoning_enabled``
    through to the wire can honour ``separate_channel``. Other clients
    accept-and-ignore the kwarg, so the registry entry would silently
    no-op at request time.
    """
    cfg = _make_config(
        "anthropic/sonnet-thinking",
        provider="anthropic",  # <-- not in _REASONING_CAPABLE_PROVIDERS
        reasoning_mode="separate_channel",
        reasoning_assembler="deepseek_v4",
        reasoning_param={"enabled": True, "effort": "high"},
        schema_mode_when_reasoning="json_object",
    )
    registry = {cfg.model_id: cfg}
    with pytest.raises(ModelRegistryError, match="provider='anthropic'"):
        validate_selected_model(registry, cfg.model_id)


def test_load_rejects_unknown_schema_mode_when_reasoning(tmp_path, monkeypatch) -> None:
    """REVUE-324 cycle 1 patch: ``schema_mode_when_reasoning`` is a closed
    enum. Anything except ``json_object`` would silently no-op at
    request-build time; fail fast at parse time instead.
    """
    from revue_core.core import models_registry as mod

    fake_yml = tmp_path / "models_registry.yml"
    fake_yml.write_text(
        "models:\n"
        "  deepseek/bogus:\n"
        "    provider: openrouter\n"
        "    schema_mode: response_format\n"
        "    schema_strict: true\n"
        "    tool_choice_first_turn: required\n"
        "    max_tokens_default: 2048\n"
        "    tier: unsupported\n"
        "    reasoning_mode: separate_channel\n"
        "    reasoning_assembler: deepseek_v4\n"
        "    reasoning_param:\n"
        "      enabled: true\n"
        "    schema_mode_when_reasoning: json_schema\n"  # <-- not allowed
    )
    monkeypatch.setattr(mod, "_BUILTIN_REGISTRY_PATH", fake_yml)
    with pytest.raises(ModelRegistryError, match="schema_mode_when_reasoning must be one of"):
        load_builtin_registry()


def test_load_rejects_reasoning_param_with_non_string_key() -> None:
    """REVUE-324 cycle 1 patch: ``reasoning_param`` is forwarded verbatim
    via ``extra_body`` to the provider HTTP body. Non-string keys would
    fail at JSON serialisation, far from the YAML source.

    YAML cannot natively express non-string mapping keys without ``!!python/…``
    tags, so build the user-override mapping in Python and stress
    ``_validate_reasoning_param_shape`` via ``merge_user_overrides``.
    """
    user = {
        "deepseek/bogus": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": True,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 2048,
            "reasoning_assembler": "deepseek_v4",
            "reasoning_mode": "separate_channel",
            "reasoning_param": {123: True},  # <-- non-string key
            "schema_mode_when_reasoning": "json_object",
        }
    }
    with pytest.raises(ModelRegistryError, match="reasoning_param keys must be strings"):
        merge_user_overrides({}, user)


def test_load_rejects_reasoning_param_with_non_json_leaf() -> None:
    """REVUE-324 cycle 1 patch: non-JSON leaf values (e.g. sets, custom
    objects) would fail at provider HTTP body serialisation.
    """
    user = {
        "deepseek/bogus": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": True,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 2048,
            "reasoning_assembler": "deepseek_v4",
            "reasoning_mode": "separate_channel",
            "reasoning_param": {"effort": {"nested"}},  # <-- set is not JSON
            "schema_mode_when_reasoning": "json_object",
        }
    }
    with pytest.raises(ModelRegistryError, match="must be a JSON scalar"):
        merge_user_overrides({}, user)


def test_load_rejects_empty_reasoning_param_dict() -> None:
    """REVUE-324 cycle 2 patch: ``reasoning_param: {}`` previously collapsed
    to ``None`` via a truthiness check, then ``validate_selected_model``
    raised the misleading ``reasoning_param is not set`` message — the
    field WAS set, just to an empty mapping. Reject the empty dict at
    parse time with a message that names the actual misconfiguration.
    """
    user = {
        "deepseek/bogus": {
            "provider": "openrouter",
            "schema_mode": "response_format",
            "schema_strict": True,
            "tool_choice_first_turn": "required",
            "max_tokens_default": 2048,
            "reasoning_assembler": "deepseek_v4",
            "reasoning_mode": "separate_channel",
            "reasoning_param": {},  # <-- empty dict — misconfiguration
            "schema_mode_when_reasoning": "json_object",
        }
    }
    with pytest.raises(ModelRegistryError, match="reasoning_param must be a non-empty mapping"):
        merge_user_overrides({}, user)


def test_models_registry_yml_copies_in_sync() -> None:
    """REVUE-324 cycle 1 patch: the vendored copy at
    ``packaging/revue/src/revue_skill/skill/_revue/models_registry.yml`` is
    regenerated by a pre-commit hook from
    ``packaging/revue_core/src/revue_core/core/models_registry.yml`` and
    MUST stay byte-identical. Drift breaks model resolution in the skill
    wheel without breaking core tests — this guard catches it.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    core_yml = (
        repo_root
        / "packaging"
        / "revue_core"
        / "src"
        / "revue_core"
        / "core"
        / "models_registry.yml"
    )
    skill_yml = (
        repo_root
        / "packaging"
        / "revue"
        / "src"
        / "revue_skill"
        / "skill"
        / "_revue"
        / "models_registry.yml"
    )
    assert core_yml.exists(), f"missing core yml at {core_yml}"
    assert skill_yml.exists(), f"missing skill yml at {skill_yml}"
    assert core_yml.read_bytes() == skill_yml.read_bytes(), (
        "models_registry.yml copies have drifted. Regenerate the vendored "
        f"copy at {skill_yml} from the source at {core_yml}."
    )

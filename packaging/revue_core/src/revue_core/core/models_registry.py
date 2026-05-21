#!/usr/bin/env python3
"""Per-model registry + dispatcher gate (REVUE-262).

Built-in registry lives in :file:`models_registry.yml` next to this module.
Customers extend or override entries via the ``models:`` section of
``.revue.yml``; :func:`merge_user_overrides` produces a fresh, immutable
view without mutating the built-in dict.

The dispatcher calls :func:`validate_selected_model` at startup; it raises
:class:`ModelRegistryError` if the selected model is unknown, or if a
``tier: supported`` entry has lost its strict schema guarantee.

Design notes
------------
- :class:`ModelConfig` is a *frozen* dataclass; an ``extras`` dict captures
  forward-compat knob keys we don't recognise yet (e.g. when a newer
  registry mentions a knob this binary doesn't read).
- All public functions are pure — no module-level state, no singletons.
- Customer-added entries default to ``tier: unsupported`` so users can
  bring-their-own model without tripping the strictness gate, but if they
  explicitly mark a new entry ``tier: supported`` the gate applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelConfig:
    """Immutable per-model knob bundle.

    ``extras`` is wrapped in :class:`types.MappingProxyType` after construction
    so the frozen dataclass guarantee extends to the nested mapping — callers
    cannot mutate forward-compat knobs in place.
    """

    model_id: str
    provider: str
    schema_mode: str
    schema_strict: bool
    tool_choice_first_turn: str
    max_tokens_default: int
    tier: str
    extras: Mapping[str, Any] = field(default_factory=dict)
    # REVUE-324 — Reasoning channel knobs (Vex Option C).
    # Defaults are a strict no-op: every existing client keeps today's wire
    # shape unless the entry opts in by naming an assembler.
    reasoning_assembler: str | None = None
    reasoning_mode: str = "none"
    reasoning_param: Mapping[str, Any] | None = None
    schema_mode_when_reasoning: str | None = None

    def __post_init__(self) -> None:
        # Wrap extras in a read-only view. Use object.__setattr__ to bypass
        # frozen-dataclass write protection (the standard pattern).
        if not isinstance(self.extras, MappingProxyType):
            object.__setattr__(
                self, "extras", MappingProxyType(dict(self.extras))
            )

    def __getstate__(self) -> dict[str, Any]:
        # ``MappingProxyType`` does not pickle/deepcopy reliably across all
        # CPython versions. Convert ``extras`` to a plain dict for serialise;
        # ``__setstate__`` restores the read-only wrapper. Without this,
        # ``copy.deepcopy(cfg)`` is one CPython patch away from ``TypeError``.
        state = dict(self.__dict__)
        state["extras"] = dict(self.extras)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        extras = state.get("extras", {})
        state["extras"] = MappingProxyType(dict(extras))
        self.__dict__.update(state)


class ModelRegistryError(ValueError):
    """Raised when the selected model is missing or fails the gate."""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_BUILTIN_REGISTRY_PATH: Path = Path(__file__).parent / "models_registry.yml"

# Known knob keys; anything else goes into `extras`.
_KNOWN_KEYS: frozenset[str] = frozenset({
    "provider",
    "schema_mode",
    "schema_strict",
    "tool_choice_first_turn",
    "max_tokens_default",
    "tier",
    "reasoning_assembler",
    "reasoning_mode",
    "reasoning_param",
    "schema_mode_when_reasoning",
})

# Allowed values for the `tier` knob. Anything else is rejected at parse time.
_VALID_TIERS: frozenset[str] = frozenset({"supported", "unsupported"})

# REVUE-324 — Reasoning channel knobs (Vex Option C).
#
# ``_VALID_REASONING_MODES`` is the closed enum for the ``reasoning_mode``
# knob today. ``"inline"`` and ``"anthropic_thinking"`` are deliberately
# absent — they're follow-up tickets and accepting them here would
# silently no-op until matching assemblers are wired, which is worse than
# failing fast.
_VALID_REASONING_MODES: frozenset[str] = frozenset({"none", "separate_channel"})

# Closed enum for ``schema_mode_when_reasoning``. Today only ``json_object``
# is honoured at the wire (ai_client routes it into ``response_format``).
# Anything else would parse-validate fine and then silently no-op at
# request-build time; enforcing the enum here fails fast instead.
_VALID_SCHEMA_MODES_WHEN_REASONING: frozenset[str] = frozenset({"json_object"})

# Providers whose client implementation actually wires ``reasoning_enabled``
# through to the wire. Today only ``openrouter`` does. A registry entry
# that sets ``reasoning_mode=separate_channel`` on another provider would
# accept the config and silently no-op at request time, which is the
# failure mode the lock-step contract exists to prevent.
_REASONING_CAPABLE_PROVIDERS: frozenset[str] = frozenset({"openrouter"})

# Allowed leaf-value types for ``reasoning_param`` after dict-mapping
# normalisation. The payload travels verbatim through ``extra_body`` to
# the provider's HTTP body, so anything that won't JSON-serialise will
# fail far from the YAML source.
_JSON_LEAF_TYPES: tuple = (str, int, bool, float)

# Source of truth for known assembler names. ``ai_client._REASONING_ASSEMBLERS``
# asserts its dict keys match this frozenset at module load — the two
# stay in lock-step. Keeping the set here (not in ai_client) avoids a
# circular import: ai_client.py imports from models_registry.py.
_VALID_REASONING_ASSEMBLERS: frozenset[str] = frozenset({"deepseek_v4"})


def _validate_reasoning_param_shape(
    model_id: str, payload: dict, _path: str = "reasoning_param"
) -> None:
    """Recursively assert ``payload`` is a JSON-safe ``dict[str, …]`` tree.

    Keys must be ``str``; leaf values must be one of ``_JSON_LEAF_TYPES``
    or nested dicts of the same. Lists are permitted with the same leaf
    rules. ``None`` is allowed for leaves.
    """
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ModelRegistryError(
                f"models.{model_id}.{_path} keys must be strings, "
                f"got {type(key).__name__}: {key!r}."
            )
        sub_path = f"{_path}.{key}"
        if value is None or isinstance(value, _JSON_LEAF_TYPES):
            continue
        if isinstance(value, dict):
            _validate_reasoning_param_shape(model_id, value, sub_path)
            continue
        if isinstance(value, list):
            for idx, item in enumerate(value):
                if item is None or isinstance(item, _JSON_LEAF_TYPES):
                    continue
                if isinstance(item, dict):
                    _validate_reasoning_param_shape(
                        model_id, item, f"{sub_path}[{idx}]"
                    )
                    continue
                raise ModelRegistryError(
                    f"models.{model_id}.{sub_path}[{idx}] must be a JSON scalar "
                    f"or dict, got {type(item).__name__}."
                )
            continue
        raise ModelRegistryError(
            f"models.{model_id}.{sub_path} must be a JSON scalar, dict, or list, "
            f"got {type(value).__name__}."
        )


def _config_from_mapping(
    model_id: str,
    raw: dict[str, Any],
    *,
    default_tier: str,
    base: ModelConfig | None = None,
) -> ModelConfig:
    """Build a ModelConfig from a raw mapping.

    When *base* is supplied, missing keys fall back to that base's values
    (per-entry override semantics). Otherwise sane defaults apply for the
    customer-added case.
    """
    def pick(key: str, fallback: Any) -> Any:
        if key in raw and raw[key] is not None:
            return raw[key]
        if base is not None:
            return getattr(base, key)
        return fallback

    extras: dict[str, Any] = dict(base.extras) if base is not None else {}
    for k, v in raw.items():
        if k not in _KNOWN_KEYS:
            extras[k] = v

    # --- strict type checks on policy-bearing fields ---
    raw_schema_strict = pick("schema_strict", False)
    # ``bool`` is a subclass of ``int``; the isinstance check below is intentional.
    # Reject strings like "true"/"false" — they used to slip through ``bool(...)``.
    if not isinstance(raw_schema_strict, bool):
        raise ModelRegistryError(
            f"models.{model_id}.schema_strict must be a boolean (true/false), "
            f"got {type(raw_schema_strict).__name__}: {raw_schema_strict!r}."
        )

    raw_tier = pick("tier", default_tier)
    # Symmetry with ``schema_strict``: require a string before coercing.
    # Without this, YAML scalars like ``tier: 1`` would coerce to ``"1"`` and
    # then fail the enum check with a confusing ``got 1`` message.
    if not isinstance(raw_tier, str):
        raise ModelRegistryError(
            f"models.{model_id}.tier must be a string "
            f"('supported' or 'unsupported'), got {raw_tier!r}."
        )
    tier_value = raw_tier.lower()
    if tier_value not in _VALID_TIERS:
        raise ModelRegistryError(
            f"models.{model_id}.tier must be one of "
            f"{{supported, unsupported}}, got {raw_tier!r}."
        )

    # --- REVUE-324: reasoning-channel knob validation (parse-time) ---
    raw_reasoning_mode = pick("reasoning_mode", "none")
    if not isinstance(raw_reasoning_mode, str):
        raise ModelRegistryError(
            f"models.{model_id}.reasoning_mode must be a string, "
            f"got {type(raw_reasoning_mode).__name__}: {raw_reasoning_mode!r}."
        )
    if raw_reasoning_mode not in _VALID_REASONING_MODES:
        raise ModelRegistryError(
            f"models.{model_id}.reasoning_mode must be one of "
            f"{sorted(_VALID_REASONING_MODES)}, got {raw_reasoning_mode!r}."
        )

    raw_reasoning_assembler = pick("reasoning_assembler", None)
    if raw_reasoning_assembler is not None:
        if not isinstance(raw_reasoning_assembler, str):
            raise ModelRegistryError(
                f"models.{model_id}.reasoning_assembler must be a string or null, "
                f"got {type(raw_reasoning_assembler).__name__}."
            )
        if raw_reasoning_assembler not in _VALID_REASONING_ASSEMBLERS:
            raise ModelRegistryError(
                f"models.{model_id}.reasoning_assembler {raw_reasoning_assembler!r} "
                f"is not registered. Known assemblers: "
                f"{sorted(_VALID_REASONING_ASSEMBLERS)}."
            )

    raw_reasoning_param = pick("reasoning_param", None)
    if raw_reasoning_param is not None and not isinstance(raw_reasoning_param, dict):
        raise ModelRegistryError(
            f"models.{model_id}.reasoning_param must be a mapping or null, "
            f"got {type(raw_reasoning_param).__name__}."
        )
    if isinstance(raw_reasoning_param, dict):
        # An empty dict would assemble to ``{"reasoning": {}}`` on the
        # wire — a misconfiguration that ``validate_selected_model`` used
        # to surface with the misleading "not set" error (because a prior
        # truthiness collapse turned ``{}`` into ``None``). Reject at the
        # YAML source instead.
        if not raw_reasoning_param:
            raise ModelRegistryError(
                f"models.{model_id}.reasoning_param must be a non-empty mapping "
                f"when present; got an empty dict."
            )
        # Keys must be strings, leaf values must be JSON-serialisable
        # scalars (or nested dicts of the same). The payload is forwarded
        # verbatim via ``extra_body`` to the provider; non-JSON values
        # would raise at HTTP-serialisation time, far from the YAML source.
        _validate_reasoning_param_shape(model_id, raw_reasoning_param)

    raw_schema_mode_when_reasoning = pick("schema_mode_when_reasoning", None)
    if raw_schema_mode_when_reasoning is not None:
        if not isinstance(raw_schema_mode_when_reasoning, str):
            raise ModelRegistryError(
                f"models.{model_id}.schema_mode_when_reasoning must be a string or "
                f"null, got {type(raw_schema_mode_when_reasoning).__name__}."
            )
        if raw_schema_mode_when_reasoning not in _VALID_SCHEMA_MODES_WHEN_REASONING:
            raise ModelRegistryError(
                f"models.{model_id}.schema_mode_when_reasoning must be one of "
                f"{sorted(_VALID_SCHEMA_MODES_WHEN_REASONING)}, "
                f"got {raw_schema_mode_when_reasoning!r}."
            )

    return ModelConfig(
        model_id=model_id,
        provider=str(pick("provider", "")),
        schema_mode=str(pick("schema_mode", "")),
        schema_strict=raw_schema_strict,
        tool_choice_first_turn=str(pick("tool_choice_first_turn", "auto")),
        max_tokens_default=int(pick("max_tokens_default", 1024)),
        tier=tier_value,
        extras=extras,
        reasoning_assembler=raw_reasoning_assembler,
        reasoning_mode=raw_reasoning_mode,
        reasoning_param=dict(raw_reasoning_param) if raw_reasoning_param is not None else None,
        schema_mode_when_reasoning=raw_schema_mode_when_reasoning,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_builtin_registry() -> dict[str, ModelConfig]:
    """Parse :file:`models_registry.yml` and return ``{model_id: ModelConfig}``.

    Unknown knob keys land in ``ModelConfig.extras`` for forward compat.

    Any :class:`OSError` (e.g. missing file) or :class:`yaml.YAMLError`
    (malformed registry shipped in a broken install) is re-raised as
    :class:`ModelRegistryError` so the install-shape regression surfaces
    cleanly at startup rather than as a low-level traceback.
    """
    try:
        with open(_BUILTIN_REGISTRY_PATH) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ModelRegistryError(
            f"failed to load built-in registry from {_BUILTIN_REGISTRY_PATH}: {exc}"
        ) from exc

    models: dict[str, Any] = raw.get("models", {}) or {}
    out: dict[str, ModelConfig] = {}
    for model_id, entry in models.items():
        if not isinstance(entry, dict):
            raise ModelRegistryError(
                f"models_registry.yml: entry for {model_id!r} must be a mapping."
            )
        out[str(model_id)] = _config_from_mapping(
            str(model_id), entry, default_tier="supported"
        )
    return out


def merge_user_overrides(
    builtin: dict[str, ModelConfig],
    user_models: dict[str, dict[str, Any]],
) -> dict[str, ModelConfig]:
    """Return a fresh registry: built-in entries overlaid with user edits.

    - Per-entry override: if ``user_models[id]`` exists for a built-in id,
      only the supplied keys change; the rest fall back to the built-in.
    - Customer-added entry: ids not in *builtin* are added with
      ``tier: unsupported`` by default. An explicit ``tier:`` in the user
      mapping wins.
    - Pure: *builtin* is not mutated.
    """
    merged: dict[str, ModelConfig] = dict(builtin)

    # Caller (``config_loader._load_and_validate_model_registry``) short-circuits
    # on a falsy ``user_models`` and the public type is ``dict[...]``, so we
    # iterate it directly — no defensive ``or {}``.
    for model_id, entry in user_models.items():
        if not isinstance(entry, dict):
            raise ModelRegistryError(
                f".revue.yml: models.{model_id} must be a mapping."
            )
        base = merged.get(model_id)
        if base is not None:
            merged[model_id] = _config_from_mapping(
                model_id, entry, default_tier=base.tier, base=base
            )
        else:
            merged[model_id] = _config_from_mapping(
                model_id, entry, default_tier="unsupported"
            )
    return merged


def validate_selected_model(
    registry: dict[str, ModelConfig],
    selected_model_id: str,
) -> ModelConfig:
    """Dispatcher gate. Returns the resolved ModelConfig or raises.

    Raises :class:`ModelRegistryError` when:
    - *selected_model_id* is absent from *registry*.
    - The resolved entry has ``tier == "supported"`` but
      ``schema_strict is not True``.

    Customer-added (``tier == "unsupported"``) entries pass silently — no
    warning, no error (locked policy).
    """
    cfg = registry.get(selected_model_id)
    if cfg is None:
        known = ", ".join(sorted(registry.keys())) or "<empty registry>"
        raise ModelRegistryError(
            f"unknown model {selected_model_id!r}. Known models: {known}."
        )
    if cfg.tier == "supported" and cfg.schema_strict is not True:
        raise ModelRegistryError(
            f"model {selected_model_id!r} is tier=supported but has "
            f"schema_strict={cfg.schema_strict!r}; supported models must keep "
            f"schema_strict=true."
        )
    # REVUE-324: co-requirements on the reasoning-channel knobs.
    # ``separate_channel`` only makes sense when an assembler name,
    # schema-mode-override, AND reasoning_param are all set; a partial set
    # would silently no-op (or emit ``{"reasoning": {}}`` on the wire,
    # which providers may reject) at request-build time. The provider
    # must also be in ``_REASONING_CAPABLE_PROVIDERS`` — only OpenRouter
    # wires ``reasoning_enabled`` through today.
    if cfg.reasoning_mode == "separate_channel":
        if cfg.provider not in _REASONING_CAPABLE_PROVIDERS:
            raise ModelRegistryError(
                f"model {selected_model_id!r} has reasoning_mode=separate_channel "
                f"but provider={cfg.provider!r} is not one of "
                f"{sorted(_REASONING_CAPABLE_PROVIDERS)}; the reasoning channel "
                f"would silently no-op on this provider."
            )
        if cfg.schema_mode_when_reasoning is None:
            raise ModelRegistryError(
                f"model {selected_model_id!r} has reasoning_mode=separate_channel "
                f"but schema_mode_when_reasoning is not set; all three are required."
            )
        if cfg.reasoning_assembler is None:
            raise ModelRegistryError(
                f"model {selected_model_id!r} has reasoning_mode=separate_channel "
                f"but reasoning_assembler is not set; all three are required."
            )
        if cfg.reasoning_param is None:
            raise ModelRegistryError(
                f"model {selected_model_id!r} has reasoning_mode=separate_channel "
                f"but reasoning_param is not set; all three are required."
            )
    return cfg

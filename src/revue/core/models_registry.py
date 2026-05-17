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
})

# Allowed values for the `tier` knob. Anything else is rejected at parse time.
_VALID_TIERS: frozenset[str] = frozenset({"supported", "unsupported"})


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

    return ModelConfig(
        model_id=model_id,
        provider=str(pick("provider", "")),
        schema_mode=str(pick("schema_mode", "")),
        schema_strict=raw_schema_strict,
        tool_choice_first_turn=str(pick("tool_choice_first_turn", "auto")),
        max_tokens_default=int(pick("max_tokens_default", 1024)),
        tier=tier_value,
        extras=extras,
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
    return cfg

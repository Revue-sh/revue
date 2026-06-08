"""Per-surface agent_timeout_seconds defaults (REVUE-341).

Surface auto-detection uses only environment variables that are exclusive to a
given deployment context:

  - ci:  BITBUCKET_BUILD_NUMBER present (set by Bitbucket Pipelines runner)
  - cli: none of the above (local developer machine or any unrecognised context)

``/revue-local`` (1200 s) is NOT auto-detected. ``APP_ENV=staging`` is an
internal variable used by the licence-validator tier-override gate and is also
injected by the GitHub Actions and GitLab CI review templates, so it cannot
uniquely identify the Revue web-app context. Projects that run inside the Revue
web app and need the 1200 s default should set it explicitly via
``review.surface_defaults`` in ``.revue.yml``.
"""

from __future__ import annotations

import os
from typing import Literal

# Valid surface name for auto-detection.
DetectedSurface = Literal["ci", "cli"]

# Full set of recognised surface names, including the user-configurable
# ``/revue-local`` key (not auto-detected but valid in review.surface_defaults).
Surface = Literal["/revue-local", "ci", "cli"]

BUILT_IN_SURFACE_DEFAULTS: dict[str, int] = {
    "/revue-local": 1200,
    "ci": 600,
    "cli": 600,
}


def detect_surface() -> DetectedSurface:
    """Return the deployment surface inferred from environment variables.

    Returns ``"ci"`` when ``BITBUCKET_BUILD_NUMBER`` is set, ``"cli"``
    otherwise.  ``APP_ENV`` is intentionally ignored — see module docstring.
    """
    if os.environ.get("BITBUCKET_BUILD_NUMBER"):
        return "ci"
    return "cli"


def resolve_surface_timeout(user_surface_defaults: dict[str, int], surface: str) -> int:
    """Return the effective timeout for *surface*.

    User-supplied per-surface overrides (from review.surface_defaults in
    .revue.yml) take precedence over the built-in defaults.  Falls back to the
    ``cli`` built-in (600 s) for any surface name not in
    ``BUILT_IN_SURFACE_DEFAULTS``.
    """
    if surface in user_surface_defaults:
        return user_surface_defaults[surface]
    return BUILT_IN_SURFACE_DEFAULTS.get(surface, BUILT_IN_SURFACE_DEFAULTS["cli"])

"""Unit tests for revue_core.core.surface_defaults (REVUE-341).

Covers:
- detect_surface() returns correct surface based on env vars (ci or cli only)
- APP_ENV is intentionally ignored by detect_surface()
- resolve_surface_timeout() applies user overrides and built-in defaults
- BUILT_IN_SURFACE_DEFAULTS contains expected values
"""

import pytest

from revue_core.core.surface_defaults import (
    BUILT_IN_SURFACE_DEFAULTS,
    detect_surface,
    resolve_surface_timeout,
)


# ---------------------------------------------------------------------------
# detect_surface()
# ---------------------------------------------------------------------------


def test_surface_detection_bitbucket_build_number_set_returns_ci(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "42")
    assert detect_surface() == "ci"


def test_surface_detection_no_env_vars_returns_cli(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("BITBUCKET_BUILD_NUMBER", raising=False)
    assert detect_surface() == "cli"


def test_surface_detection_app_env_staging_is_ignored_returns_cli(monkeypatch):
    """APP_ENV=staging is used by the licence-validator tier-override gate and
    by the GitHub/GitLab CI templates — it cannot uniquely identify /revue-local,
    so detect_surface() ignores it entirely."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("BITBUCKET_BUILD_NUMBER", raising=False)
    assert detect_surface() == "cli"


def test_surface_detection_app_env_staging_with_ci_returns_ci(monkeypatch):
    """BITBUCKET_BUILD_NUMBER wins; APP_ENV=staging is irrelevant."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "99")
    assert detect_surface() == "ci"


def test_surface_detection_app_env_production_with_ci_returns_ci(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BITBUCKET_BUILD_NUMBER", "1")
    assert detect_surface() == "ci"


def test_surface_detection_app_env_production_no_ci_returns_cli(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("BITBUCKET_BUILD_NUMBER", raising=False)
    assert detect_surface() == "cli"


# ---------------------------------------------------------------------------
# BUILT_IN_SURFACE_DEFAULTS values
# ---------------------------------------------------------------------------


def test_built_in_revue_local_default_is_1200():
    assert BUILT_IN_SURFACE_DEFAULTS["/revue-local"] == 1200


def test_built_in_ci_default_is_600():
    assert BUILT_IN_SURFACE_DEFAULTS["ci"] == 600


def test_built_in_cli_default_is_600():
    assert BUILT_IN_SURFACE_DEFAULTS["cli"] == 600


# ---------------------------------------------------------------------------
# resolve_surface_timeout()
# ---------------------------------------------------------------------------


def test_resolve_uses_built_in_when_no_user_override():
    assert resolve_surface_timeout({}, "cli") == 600
    assert resolve_surface_timeout({}, "ci") == 600
    assert resolve_surface_timeout({}, "/revue-local") == 1200


def test_resolve_user_override_wins_over_built_in():
    assert resolve_surface_timeout({"cli": 300}, "cli") == 300


def test_resolve_user_override_for_revue_local():
    assert resolve_surface_timeout({"/revue-local": 900}, "/revue-local") == 900


def test_resolve_partial_user_override_leaves_other_surfaces_as_built_in():
    user = {"ci": 120}
    assert resolve_surface_timeout(user, "ci") == 120
    assert resolve_surface_timeout(user, "cli") == 600
    assert resolve_surface_timeout(user, "/revue-local") == 1200


def test_resolve_unknown_surface_falls_back_to_cli_built_in():
    """Surface names not in BUILT_IN_SURFACE_DEFAULTS fall back to cli default."""
    assert resolve_surface_timeout({}, "unknown-surface") == 600

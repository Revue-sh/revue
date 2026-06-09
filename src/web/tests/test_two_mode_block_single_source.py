"""Unit + lint guard for the Two-Mode Block partial (REVUE-408).

Two responsibilities:

1. Macro-render unit tests — assert the shared partial renders both canonical
   modes (CLI primary, CI complementary) with the stable ``data-mode`` selectors
   and a CI link to the canonical ``/docs/ci-setup`` page, in both the ``full``
   and ``compact`` variants.

2. Single-source lint guard (NOT an E2E test) — assert the two-mode copy lives
   ONLY in ``partials/_two_mode_block.html`` and is not duplicated inline in any
   other template. This runs in the plain unit/lint stage by reading template
   files off disk, so a copy drift fails fast without spinning up a server.
"""
from __future__ import annotations

import os

import pytest

from config import templates

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
PARTIAL_REL = os.path.join("partials", "_two_mode_block.html")


def _render(**kwargs) -> str:
    """Render the two_mode_block macro in isolation and return the HTML.

    The macro takes ``ci_setup_url`` as a parameter (callers pass
    ``url_for('ci_setup')`` from their own request-bound context — Jinja macros
    do not inherit the caller's context, so resolving ``url_for`` inside the
    macro would raise KeyError('request')). The default ``/docs/ci-setup`` lets
    the macro render standalone here; the real ``url_for('ci_setup')`` →
    ``/docs/ci-setup`` wiring is exercised end-to-end by the E2E suite and the
    per-surface render tests.
    """
    src = (
        "{% from 'partials/_two_mode_block.html' import two_mode_block %}"
        "{{ two_mode_block(**kwargs) }}"
    )
    return templates.env.from_string(src).render(kwargs=kwargs)


# ---------------------------------------------------------------------------
# Macro render — both modes present, CLI primary, CI links to /docs/ci-setup
# ---------------------------------------------------------------------------
def test_full_variant_renders_both_modes():
    html = _render(variant="full")
    assert 'data-mode="cli"' in html
    assert 'data-mode="ci"' in html


def test_full_variant_cli_listed_before_ci():
    html = _render(variant="full")
    assert html.index('data-mode="cli"') < html.index('data-mode="ci"')


def test_full_variant_references_revue_skill():
    # The FULL variant (landing) references the /revue Claude Code skill invocation.
    html = _render(variant="full")
    assert "/revue" in html


def test_compact_variant_cli_block_describes_local_without_bare_command():
    # The COMPACT variant (dashboard / account-plan / activate) describes the
    # local/pre-commit mode but must NOT emit the bare `revue activate` command:
    # that command belongs to the state-gated Command-Box widget, and the Free
    # account-plan state must show no activation command (REVUE-382 AC).
    html = _render(variant="compact")
    cli_block = html[html.index('data-mode="cli"'):html.index('data-mode="ci"')]
    assert "locally" in cli_block.lower()
    assert "before you commit" in cli_block.lower()
    assert "revue activate" not in html


def test_full_variant_ci_links_to_ci_setup_page():
    html = _render(variant="full")
    assert "/docs/ci-setup" in html


def test_compact_variant_renders_both_modes():
    html = _render(variant="compact")
    assert 'data-mode="cli"' in html
    assert 'data-mode="ci"' in html
    assert "/docs/ci-setup" in html


def test_compact_variant_cli_listed_before_ci():
    html = _render(variant="compact")
    assert html.index('data-mode="cli"') < html.index('data-mode="ci"')


# ---------------------------------------------------------------------------
# Single-source lint guard — the two-mode copy lives ONLY in the partial
# ---------------------------------------------------------------------------
# Canonical markers that identify the two-mode block. These must NOT appear in
# any template other than the partial itself. We key on the stable ``data-mode``
# selectors (the AC's copy-survivable markers) — NOT on generic words like
# "CLI"/"CI", nor on the "Two ways to use Revue" heading, which pre-existing
# out-of-scope surfaces (billing_success, onboarding) legitimately reuse as a
# short heading without rendering the full two-mode block.
TWO_MODE_MARKERS = (
    'data-mode="cli"',
    'data-mode="ci"',
)


def _all_template_files() -> list[str]:
    paths = []
    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for name in files:
            if name.endswith(".html"):
                paths.append(os.path.join(root, name))
    return paths


@pytest.mark.parametrize("marker", TWO_MODE_MARKERS)
def test_two_mode_marker_only_in_partial(marker):
    partial_abs = os.path.normpath(os.path.join(TEMPLATES_DIR, PARTIAL_REL))
    offenders = []
    for path in _all_template_files():
        if os.path.normpath(path) == partial_abs:
            continue
        with open(path, encoding="utf-8") as fh:
            if marker in fh.read():
                offenders.append(os.path.relpath(path, TEMPLATES_DIR))
    assert not offenders, (
        f"Two-mode marker {marker!r} is duplicated inline in: {offenders}. "
        "The two-mode copy must live ONLY in partials/_two_mode_block.html — "
        "include the partial instead of copying its content."
    )


def test_partial_itself_contains_the_markers():
    """Sanity: the partial is the single source, so it must carry the markers."""
    partial_abs = os.path.normpath(os.path.join(TEMPLATES_DIR, PARTIAL_REL))
    with open(partial_abs, encoding="utf-8") as fh:
        content = fh.read()
    for marker in TWO_MODE_MARKERS:
        assert marker in content

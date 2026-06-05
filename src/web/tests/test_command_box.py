"""Unit tests for the reusable Activation Command-Box macro (REVUE-384).

The Command-Box is a shared Jinja macro consumed by /activate (default state)
and, later, the Account→Plan page (masked state). Because the masked state is
owned by that not-yet-built consumer — and /activate must stay unauthenticated
(AC1) with no request input in its render context — the masked state is covered
here at the macro-render level rather than via an /activate E2E. These tests
assert AC7 (masked visible text vs. full copy payload) and pin the XSS
regression (autoescaping of attacker-controlled values).
"""
from __future__ import annotations

import re

import pytest

from config import templates


def _render(**kwargs) -> str:
    """Render the command_box macro in isolation and return the HTML."""
    src = (
        "{% from 'partials/command_box.html' import command_box %}"
        "{{ command_box(**kwargs) }}"
    )
    return templates.env.from_string(src).render(kwargs=kwargs)


def _visible_command(html: str) -> str:
    """Extract the text inside the .command-box-command <code> element."""
    m = re.search(r'command-box-command[^>]*>(.*?)</code>', html, re.S)
    assert m, f"no .command-box-command element in:\n{html}"
    return m.group(1)


def _copy_payload(html: str) -> str:
    m = re.search(r'data-copy-payload="(.*?)"', html, re.S)
    assert m, f"no data-copy-payload attribute in:\n{html}"
    return m.group(1)


# ---------------------------------------------------------------------------
# Default state — full command visible; Copy yields the same string
# ---------------------------------------------------------------------------
def test_default_state_shows_command_verbatim():
    html = _render(
        dom_id="box",
        command="revue activate lic_" + "a" * 32,
    )
    assert _visible_command(html).strip() == "revue activate lic_" + "a" * 32
    # Copy payload defaults to the command when copy_payload is omitted.
    assert _copy_payload(html).strip() == "revue activate lic_" + "a" * 32


def test_default_state_copy_payload_overrides_visible():
    html = _render(dom_id="box", command="run this", copy_payload="the-real-thing")
    assert _visible_command(html).strip() == "run this"
    assert _copy_payload(html).strip() == "the-real-thing"


# ---------------------------------------------------------------------------
# AC7 — masked state: lic_••••<last-4> visible; Copy yields the FULL key
# ---------------------------------------------------------------------------
def test_masked_state_shows_dots_but_payload_is_full_key():
    full_key = "lic_" + "0123456789abcdef0123456789abcdef"
    html = _render(
        dom_id="plan-key",
        copy_payload=full_key,
        masked=True,
        masked_display="lic_••••" + full_key[-4:],
    )

    visible = _visible_command(html)
    # The visible DOM shows only the masked form.
    assert visible.strip() == "lic_••••" + full_key[-4:]
    # The full key NEVER appears as visible text.
    assert full_key not in visible
    # Copy still yields the full, unmasked key.
    assert _copy_payload(html).strip() == full_key


def test_masked_state_marks_data_masked():
    html = _render(
        dom_id="plan-key",
        copy_payload="lic_" + "f" * 32,
        masked=True,
        masked_display="lic_••••ffff",
    )
    assert 'data-masked="true"' in html


# ---------------------------------------------------------------------------
# XSS regression (review finding #3) — autoescaping must neutralise hostile
# input in BOTH the attribute context (data-copy-payload) and the text context.
# ---------------------------------------------------------------------------
def test_payload_with_quote_is_escaped_in_attribute():
    # A double quote would break out of data-copy-payload="..." if unescaped.
    hostile = 'x" onmouseover="alert(1)'
    html = _render(dom_id="box", command="cmd", copy_payload=hostile)
    # The raw breakout sequence must not survive into the markup.
    assert 'onmouseover="alert(1)"' not in html
    assert "&#34;" in html or "&quot;" in html


def test_visible_command_with_angle_bracket_is_escaped():
    hostile = "<script>alert(1)</script>"
    html = _render(dom_id="box", command=hostile)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_masked_display_is_escaped():
    hostile = "<b>lic</b>"
    html = _render(
        dom_id="box", copy_payload="lic_" + "a" * 32, masked=True, masked_display=hostile
    )
    assert "<b>lic</b>" not in html
    assert "&lt;b&gt;" in html


# ---------------------------------------------------------------------------
# Reusability — ids are derived from dom_id (review finding #5)
# ---------------------------------------------------------------------------
def test_ids_are_namespaced_off_dom_id():
    html = _render(dom_id="my-unique-box", command="cmd")
    assert 'id="my-unique-box"' in html
    assert 'id="my-unique-box-copy"' in html


def test_two_instances_have_distinct_ids():
    html = _render(dom_id="box-a", command="a") + _render(dom_id="box-b", command="b")
    assert 'id="box-a"' in html and 'id="box-b"' in html
    assert 'id="box-a-copy"' in html and 'id="box-b-copy"' in html


# ---------------------------------------------------------------------------
# Copy button wires copyToClipboard with the 'Copied! ✓' label (AC6)
# ---------------------------------------------------------------------------
def test_copy_button_passes_checkmark_label():
    html = _render(dom_id="box", command="cmd")
    assert "copyToClipboard(" in html
    assert "Copied! ✓" in html

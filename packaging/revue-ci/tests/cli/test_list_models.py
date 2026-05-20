#!/usr/bin/env python3
"""Tests for ``revue list-models`` (REVUE-264).

The command surfaces the per-model registry so customers can discover which
models Revue ships with, what knobs each one has, and whether their local
``.revue.yml`` has overridden anything. Output modes:

* default: human-readable table
* ``--json``: machine-readable JSON array
* ``--markdown``: Markdown table (used to regenerate the README section)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from revue_ci.cli import build_parser, cmd_list_models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_in(tmp_path: Path, argv: list[str], monkeypatch) -> int:
    """Switch cwd to *tmp_path*, parse *argv*, invoke ``cmd_list_models``."""
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    args = parser.parse_args(argv)
    return cmd_list_models(args)


def _write_user_yml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".revue.yml"
    p.write_text(body)
    return p


SUPPORTED_BUILTIN_IDS = {
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    "qwen/qwen3-coder-next",
    "deepseek/deepseek-v4-pro",
}


# ---------------------------------------------------------------------------
# 1. Human output: all supported entries present
# ---------------------------------------------------------------------------


def test_list_models_human_output_includes_all_supported_entries(
    tmp_path, monkeypatch, capsys
):
    rc = _run_in(tmp_path, ["list-models"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    for model_id in SUPPORTED_BUILTIN_IDS:
        assert model_id in out, f"missing {model_id} in:\n{out}"


# ---------------------------------------------------------------------------
# 2. Human output: every knob has a column
# ---------------------------------------------------------------------------


def test_list_models_human_output_includes_columns_for_each_knob(
    tmp_path, monkeypatch, capsys
):
    rc = _run_in(tmp_path, ["list-models"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    # Header row must include each documented knob column.
    for header in (
        "Model",
        "Provider",
        "Tier",
        "schema_strict",
        "tool_choice_first_turn",
        "max_tokens_default",
    ):
        assert header in out, f"missing column header {header!r} in:\n{out}"


# ---------------------------------------------------------------------------
# 3. JSON output: parseable
# ---------------------------------------------------------------------------


def test_list_models_json_output_is_valid_json(tmp_path, monkeypatch, capsys):
    rc = _run_in(tmp_path, ["list-models", "--json"], monkeypatch)
    assert rc == 0
    raw = capsys.readouterr().out
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert len(parsed) >= 4


# ---------------------------------------------------------------------------
# 4. JSON output: every ModelConfig field is exposed
# ---------------------------------------------------------------------------


def test_list_models_json_output_contains_all_modelconfig_fields(
    tmp_path, monkeypatch, capsys
):
    rc = _run_in(tmp_path, ["list-models", "--json"], monkeypatch)
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    required = {
        "model_id",
        "provider",
        "schema_mode",
        "schema_strict",
        "tool_choice_first_turn",
        "max_tokens_default",
        "tier",
    }
    for entry in parsed:
        missing = required - entry.keys()
        assert not missing, f"entry {entry!r} missing fields {missing}"


# ---------------------------------------------------------------------------
# 5. Markdown output: renders as a Markdown table
# ---------------------------------------------------------------------------


def test_list_models_markdown_output_renders_as_table(
    tmp_path, monkeypatch, capsys
):
    rc = _run_in(tmp_path, ["list-models", "--markdown"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # First non-empty line is the header row.
    assert lines[0].startswith("|") and lines[0].endswith("|"), (
        f"header row not a pipe-delimited markdown row: {lines[0]!r}"
    )
    # Second line must be the GitHub-flavoured separator row (e.g. ``|---|---|``).
    separator = lines[1]
    assert separator.startswith("|") and "---" in separator, (
        f"separator row missing or malformed: {separator!r}"
    )
    # At least one body row per supported model.
    body = "\n".join(lines[2:])
    for model_id in SUPPORTED_BUILTIN_IDS:
        assert model_id in body, f"missing {model_id} in markdown body:\n{body}"


# ---------------------------------------------------------------------------
# 6. Annotates user overrides
# ---------------------------------------------------------------------------


def test_list_models_annotates_user_overrides(tmp_path, monkeypatch, capsys):
    # Override max_tokens_default on a built-in supported model.
    _write_user_yml(
        tmp_path,
        "version: \"1\"\n"
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-haiku-4-5-20251001\n"
        "models:\n"
        "  claude-haiku-4-5-20251001:\n"
        "    max_tokens_default: 9999\n",
    )

    rc = _run_in(tmp_path, ["list-models"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    # The override marker must appear somewhere on the line for that model.
    haiku_lines = [ln for ln in out.splitlines() if "claude-haiku-4-5-20251001" in ln]
    assert haiku_lines, f"haiku row not found in:\n{out}"
    assert any("*" in ln for ln in haiku_lines), (
        f"expected override marker '*' on haiku row, got:\n{haiku_lines!r}"
    )
    # The new value must appear.
    assert "9999" in out


# ---------------------------------------------------------------------------
# 7. Customer-added unsupported model
# ---------------------------------------------------------------------------


def test_list_models_shows_customer_added_unsupported_model(
    tmp_path, monkeypatch, capsys
):
    _write_user_yml(
        tmp_path,
        "version: \"1\"\n"
        "ai:\n"
        "  provider: openrouter\n"
        "  model: claude-haiku-4-5-20251001\n"
        "models:\n"
        "  my-org/private-model:\n"
        "    provider: custom\n"
        "    schema_mode: response_format\n"
        "    schema_strict: false\n"
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n",
    )

    rc = _run_in(tmp_path, ["list-models"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    assert "my-org/private-model" in out
    assert "unsupported" in out


# ---------------------------------------------------------------------------
# 7b. JSON output exposes override metadata
# ---------------------------------------------------------------------------


def test_list_models_json_exposes_override_metadata(
    tmp_path, monkeypatch, capsys
):
    _write_user_yml(
        tmp_path,
        "version: \"1\"\n"
        "ai:\n"
        "  provider: anthropic\n"
        "  model: claude-haiku-4-5-20251001\n"
        "models:\n"
        "  claude-haiku-4-5-20251001:\n"
        "    max_tokens_default: 9999\n"
        "  my-org/private:\n"
        "    provider: custom\n"
        "    schema_mode: response_format\n"
        "    schema_strict: false\n"
        "    tool_choice_first_turn: auto\n"
        "    max_tokens_default: 1024\n",
    )

    rc = _run_in(tmp_path, ["list-models", "--json"], monkeypatch)
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    by_id = {entry["model_id"]: entry for entry in parsed}

    haiku = by_id["claude-haiku-4-5-20251001"]
    assert haiku["_customer_added"] is False
    assert "max_tokens_default" in haiku["_overridden_fields"]

    private = by_id["my-org/private"]
    assert private["_customer_added"] is True
    # Customer-added rows expose the flag but leave _overridden_fields empty;
    # every knob is "new" relative to no-built-in, so listing them adds noise.
    assert private["_overridden_fields"] == []


# ---------------------------------------------------------------------------
# 8. Works without a .revue.yml
# ---------------------------------------------------------------------------


def test_list_models_works_without_revue_yml(tmp_path, monkeypatch, capsys):
    assert not (tmp_path / ".revue.yml").exists()
    rc = _run_in(tmp_path, ["list-models"], monkeypatch)
    assert rc == 0
    out = capsys.readouterr().out
    # Still prints the built-in supported entries.
    for model_id in SUPPORTED_BUILTIN_IDS:
        assert model_id in out

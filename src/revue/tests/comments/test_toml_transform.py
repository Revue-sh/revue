"""Unit tests for JSON → TOML prompt transform (REVUE-110 AC7 / TC6)."""
from __future__ import annotations

import tomllib
import pytest

from revue.comments.toml_transform import comment_json_to_toml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_JSON = {
    "pr_number": 42,
    "platform": "bitbucket",
    "files": {
        "src/revue/core/cli.py": {
            "abc123def456789a": {
                "state": "unresolved",
                "platform_comment_id": "12345",
                "platform_thread_id": None,
                "line_number": 10,
                "comment_body": "SQL injection risk on line 10",
                "created_at": "2026-04-06T10:00:00+00:00",
                "updated_at": "2026-04-06T10:00:00+00:00",
            }
        },
        "src/revue/agents/zara.py": {
            "deadbeefcafe0001": {
                "state": "auto_resolved",
                "platform_comment_id": "67890",
                "platform_thread_id": None,
                "line_number": 55,
                "comment_body": "Unused import",
                "created_at": "2026-04-06T09:00:00+00:00",
                "updated_at": "2026-04-06T11:00:00+00:00",
            }
        },
    },
}


# ---------------------------------------------------------------------------
# TC6: json_to_toml_prompt_transform(sample_json) → valid TOML with all fields
# ---------------------------------------------------------------------------

def test_output_is_valid_toml() -> None:
    """TC6: output must be parseable TOML."""
    result = comment_json_to_toml(SAMPLE_JSON)
    parsed = tomllib.loads(result)
    assert isinstance(parsed, dict)


def test_meta_section_present_and_correct() -> None:
    """TC6: [meta] block contains pr_number and platform."""
    result = comment_json_to_toml(SAMPLE_JSON)
    parsed = tomllib.loads(result)
    assert parsed["meta"]["pr_number"] == 42
    assert parsed["meta"]["platform"] == "bitbucket"


def test_findings_array_has_all_entries() -> None:
    """TC6: one [[findings]] entry per fingerprint across all files."""
    result = comment_json_to_toml(SAMPLE_JSON)
    parsed = tomllib.loads(result)
    assert len(parsed["findings"]) == 2


def test_finding_entry_has_required_fields() -> None:
    """TC6: each finding entry must have file_path, fingerprint, line_number, state, platform_comment_id, comment_body."""
    result = comment_json_to_toml(SAMPLE_JSON)
    parsed = tomllib.loads(result)

    required = {"file_path", "fingerprint", "line_number", "state", "platform_comment_id", "comment_body"}
    for finding in parsed["findings"]:
        assert required <= set(finding.keys()), f"Missing keys in finding: {finding}"


def test_finding_values_match_source() -> None:
    """TC6: field values round-trip correctly from JSON to TOML."""
    result = comment_json_to_toml(SAMPLE_JSON)
    parsed = tomllib.loads(result)

    by_fp = {f["fingerprint"]: f for f in parsed["findings"]}
    assert by_fp["abc123def456789a"]["file_path"] == "src/revue/core/cli.py"
    assert by_fp["abc123def456789a"]["line_number"] == 10
    assert by_fp["abc123def456789a"]["state"] == "unresolved"
    assert by_fp["abc123def456789a"]["platform_comment_id"] == "12345"
    assert by_fp["abc123def456789a"]["comment_body"] == "SQL injection risk on line 10"

    assert by_fp["deadbeefcafe0001"]["state"] == "auto_resolved"
    assert by_fp["deadbeefcafe0001"]["line_number"] == 55


def test_empty_files_produces_empty_findings() -> None:
    """Edge case: no findings → empty findings array."""
    data = {"pr_number": 1, "platform": "github", "files": {}}
    result = comment_json_to_toml(data)
    parsed = tomllib.loads(result)
    assert parsed["findings"] == []


def test_missing_required_keys_raises() -> None:
    """ValueError raised when pr_number or platform is missing."""
    with pytest.raises(ValueError):
        comment_json_to_toml({"files": {}})

    with pytest.raises(ValueError):
        comment_json_to_toml({"pr_number": 1, "files": {}})


def test_state_none_defaults_to_unresolved() -> None:
    """state=None in entry → TOML finding has state='unresolved', not 'None'."""
    data = {
        "pr_number": 1,
        "platform": "github",
        "files": {
            "src/foo.py": {
                "abc123": {
                    "state": None,
                    "platform_comment_id": "1",
                    "line_number": 5,
                    "comment_body": "body",
                }
            }
        },
    }
    import tomllib
    parsed = tomllib.loads(comment_json_to_toml(data))
    assert parsed["findings"][0]["state"] == "unresolved"

"""Slice 1 — three-state envelope enforcement in /revue-local.

Validates that ``scripts/local_run.py`` parses agent output via REVUE-246's
``classify_terminal_state`` instead of the legacy ``data.get("findings", [])``
fallback. The legacy shape (raw findings array, or ``{"findings": []}``)
must be rejected as ``invalid_response_schema`` so silent agent failures
surface — exactly the AC10 disambiguation gap REVUE-246 closed.

Tests target the helper ``_classify_agent_output`` extracted into
``scripts/local_run.py``. The helper is loaded by absolute path because
``scripts/`` is not a Python package.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]
_LOCAL_RUN_PATH = _REPO_ROOT / "scripts" / "local_run.py"


def _load_local_run():
    """Load scripts/local_run.py as a module by absolute path."""
    # Ensure src/ is on sys.path so revue.* imports inside local_run work.
    src_path = str(_REPO_ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    spec = importlib.util.spec_from_file_location("revue_local_run", _LOCAL_RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def local_run():
    return _load_local_run()


# ---------------------------------------------------------------------------
# Three-state envelope acceptance
# ---------------------------------------------------------------------------


def test_classify_accepts_three_state_findings(local_run):
    """A valid findings envelope yields state='findings' with the array preserved."""
    raw = json.dumps({
        "status": "findings",
        "findings": [
            {
                "file_path": "src/revue/cli.py",
                "line_number": 42,
                "severity": "medium",
                "issue": "missing guard",
                "suggestion": "add early return",
                "confidence": 0.8,
                "category": "code-quality",
            }
        ],
    })
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "findings"
    assert len(ts.payload["findings"]) == 1
    assert ts.payload["findings"][0]["file_path"] == "src/revue/cli.py"


def test_classify_accepts_three_state_clean(local_run):
    """A valid clean envelope yields state='clean' with summary+confidence."""
    raw = json.dumps({
        "status": "clean",
        "summary": "Reviewed 4 files in src/revue/cli.py; no issues.",
        "confidence": 0.85,
    })
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "clean"
    assert ts.payload["summary"].startswith("Reviewed 4 files")
    assert ts.payload["confidence"] == 0.85


def test_classify_accepts_three_state_error(local_run):
    """A valid error envelope yields state='error' with the code preserved."""
    raw = json.dumps({
        "status": "error",
        "error": {
            "code": "tool_unavailable",
            "message": "read_file failed for 3 of 4 files",
            "iterations_used": 5,
        },
    })
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "error"
    assert ts.payload["error"]["code"] == "tool_unavailable"
    assert ts.payload["error"]["iterations_used"] == 5


# ---------------------------------------------------------------------------
# Legacy-shape rejection — the bail-out disambiguation REVUE-246 closes
# ---------------------------------------------------------------------------


def test_classify_rejects_legacy_findings_dict_shape(local_run):
    """``{"findings": []}`` (pre-REVUE-246 shape) must surface as error."""
    raw = json.dumps({"findings": []})
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "error"
    assert ts.payload["error"]["code"] == "invalid_response_schema"


def test_classify_rejects_plain_findings_array(local_run):
    """A raw JSON array (legacy local-skill shape) must surface as error.

    The pre-Slice-1 ``/revue-local`` prompt asked agents for a JSON array;
    that shape has no ``status`` discriminator and must now be rejected.
    """
    raw = json.dumps([{"file_path": "a.py", "line_number": 1, "issue": "x"}])
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "error"
    assert ts.payload["error"]["code"] == "invalid_response_schema"


def test_classify_rejects_empty_string(local_run):
    """Empty agent output must surface as ``invalid_response_schema`` — never silently 'clean'."""
    ts = local_run._classify_agent_output("")
    assert ts.state == "error"
    assert ts.payload["error"]["code"] == "invalid_response_schema"


def test_classify_rejects_unknown_status(local_run):
    """A bogus status value must be rejected with ``invalid_response_schema``."""
    raw = json.dumps({"status": "uncertain", "summary": "hmm"})
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "error"
    assert ts.payload["error"]["code"] == "invalid_response_schema"


def test_classify_strips_markdown_fences(local_run):
    """Fenced JSON output (LLM-common) is tolerated like production."""
    raw = '```json\n{"status": "clean", "summary": "ok", "confidence": 0.9}\n```'
    ts = local_run._classify_agent_output(raw)
    assert ts.state == "clean"

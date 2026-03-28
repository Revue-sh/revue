"""Tests for shared analysis."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from revue.core.shared_analysis import run_shared_analysis, SharedAnalysisResult
from revue.core.models import FileChange


def _fc(path: str) -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=5, deletions=2,
        diff="@@ -1 +1 @@\n-old\n+new",
    )


def _mock_client(response: str) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = response
    return c


_VALID_JSON = json.dumps({
    "languages": ["python"],
    "risk_areas": ["database"],
    "suggested_agents": ["maya", "zara"],
    "summary": "Adds a new database query.",
})


def test_run_shared_analysis_success():
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_JSON))
    assert result.success
    assert "python" in result.languages


def test_run_shared_analysis_extracts_all_fields():
    result = run_shared_analysis([_fc("app.py")], _mock_client(_VALID_JSON))
    assert result.risk_areas == ["database"]
    assert result.suggested_agents == ["maya", "zara"]
    assert "database query" in result.summary


def test_run_shared_analysis_fallback_on_invalid_json():
    result = run_shared_analysis([_fc("app.py")], _mock_client("not json"))
    assert not result.success
    assert result.error == "fallback"


def test_run_shared_analysis_fallback_on_api_error():
    c = MagicMock()
    c.complete.side_effect = RuntimeError("API down")
    result = run_shared_analysis([_fc("app.py")], c)
    assert not result.success


def test_run_shared_analysis_never_raises():
    c = MagicMock()
    c.complete.side_effect = Exception("boom")
    result = run_shared_analysis([_fc("app.py")], c)
    assert isinstance(result, SharedAnalysisResult)


def test_shared_analysis_result_success_true():
    r = SharedAnalysisResult(
        languages=[], risk_areas=[], suggested_agents=[], summary="ok"
    )
    assert r.success is True


def test_shared_analysis_result_success_false():
    r = SharedAnalysisResult(
        languages=[], risk_areas=[], suggested_agents=[], summary="", error="fail"
    )
    assert r.success is False


def test_fallback_result_has_all_agents():
    r = SharedAnalysisResult.fallback(["python"])
    assert set(r.suggested_agents) == {"zara", "kai", "maya", "leo"}


def test_diff_summary_truncated():
    big_diff = "\n".join(["+line"] * 200)
    fc = FileChange(
        file_path="big.py", change_type="modified",
        additions=200, deletions=0, diff=big_diff,
    )
    c = _mock_client(_VALID_JSON)
    run_shared_analysis([fc], c, max_diff_summary_lines=100)
    prompt_sent = c.complete.call_args[0][0][0]["content"]
    assert prompt_sent.count("+line") <= 100


def test_run_shared_analysis_with_multiple_files():
    files = [_fc("a.py"), _fc("b.py"), _fc("c.py")]
    c = _mock_client(_VALID_JSON)
    run_shared_analysis(files, c)
    assert c.complete.call_count == 1

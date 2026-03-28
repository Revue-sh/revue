"""Tests for hard diff limit guard."""
from __future__ import annotations

import pytest
from revue.core.diff_limit import check_diff_limit, DiffLimitResult
from revue.core.models import FileChange


def _fc(path: str, add: int, delete: int) -> FileChange:
    return FileChange(
        file_path=path, change_type="modified",
        additions=add, deletions=delete, diff=""
    )


def test_within_limit():
    changes = [_fc("a.py", 100, 50), _fc("b.py", 200, 50), _fc("c.py", 80, 20)]
    result = check_diff_limit(changes, limit=2000)
    assert not result.exceeded
    assert result.total_lines == 500


def test_at_exact_limit():
    changes = [_fc("a.py", 1000, 1000)]
    result = check_diff_limit(changes, limit=2000)
    assert not result.exceeded


def test_exceeds_limit():
    changes = [_fc("a.py", 1500, 502)]
    result = check_diff_limit(changes, limit=2000)
    assert result.exceeded
    assert result.total_lines == 2002


def test_suggestion_populated_when_exceeded():
    changes = [_fc("big.py", 1500, 600)]
    result = check_diff_limit(changes, limit=2000)
    assert result.exceeded
    assert "big.py" in result.suggestion
    assert "2100" in result.suggestion


def test_largest_files_sorted_desc():
    changes = [_fc("small.py", 10, 5), _fc("big.py", 500, 300), _fc("mid.py", 100, 50)]
    result = check_diff_limit(changes, limit=500)
    assert result.largest_files[0][0] == "big.py"
    assert result.largest_files[1][0] == "mid.py"


def test_largest_files_capped_at_five():
    changes = [_fc(f"f{i}.py", 300, 100) for i in range(10)]
    result = check_diff_limit(changes, limit=100)
    assert len(result.largest_files) <= 5


def test_exit_as_warning_property():
    changes = [_fc("a.py", 2000, 1)]
    result = check_diff_limit(changes, limit=2000)
    assert result.exit_as_warning is True


def test_empty_changes():
    result = check_diff_limit([], limit=2000)
    assert not result.exceeded
    assert result.total_lines == 0

"""Unit tests for fingerprint stability (REVUE-110 AC3)."""
from __future__ import annotations

import pytest

from revue.comments.fingerprint import fingerprint, hunk_start_for_line


SAMPLE_DIFF = """\
@@ -10,6 +10,8 @@ def foo():
     x = 1
     y = 2
+    z = 3
+    w = 4
     return x
@@ -50,4 +52,4 @@ def bar():
     a = 1
-    b = 2
+    b = 99
     return a
"""


# ---------------------------------------------------------------------------
# TC3: ±5 line offset → identical fingerprints (hunk_start is stable)
# ---------------------------------------------------------------------------

def test_fingerprint_stable_for_lines_in_same_hunk() -> None:
    """Findings at line 10, 12, 14 are all in the same hunk (+10) → same fingerprint."""
    fp_10 = fingerprint("src/foo.py", 10, SAMPLE_DIFF)
    fp_12 = fingerprint("src/foo.py", 12, SAMPLE_DIFF)
    fp_14 = fingerprint("src/foo.py", 14, SAMPLE_DIFF)
    assert fp_10 == fp_12 == fp_14


def test_fingerprint_differs_across_hunks() -> None:
    """Findings in different hunks produce different fingerprints."""
    fp_hunk1 = fingerprint("src/foo.py", 12, SAMPLE_DIFF)
    fp_hunk2 = fingerprint("src/foo.py", 54, SAMPLE_DIFF)
    assert fp_hunk1 != fp_hunk2


def test_fingerprint_differs_across_files() -> None:
    """Same line + same diff → different fingerprint for different files."""
    fp_a = fingerprint("src/a.py", 12, SAMPLE_DIFF)
    fp_b = fingerprint("src/b.py", 12, SAMPLE_DIFF)
    assert fp_a != fp_b


def test_fingerprint_stable_regardless_of_issue_text() -> None:
    """Fingerprint must NOT depend on free-text issue_text (was the old bug)."""
    fp_1 = fingerprint("src/foo.py", 12, SAMPLE_DIFF)
    fp_2 = fingerprint("src/foo.py", 12, SAMPLE_DIFF)
    assert fp_1 == fp_2  # deterministic


# ---------------------------------------------------------------------------
# hunk_start_for_line helper
# ---------------------------------------------------------------------------

def test_hunk_start_for_line_in_first_hunk() -> None:
    start = hunk_start_for_line(SAMPLE_DIFF, 12)
    assert start == 10


def test_hunk_start_for_line_in_second_hunk() -> None:
    start = hunk_start_for_line(SAMPLE_DIFF, 54)
    assert start == 52


def test_hunk_start_for_line_no_diff_falls_back_to_line() -> None:
    """When no diff is provided, fall back to the line number itself."""
    start = hunk_start_for_line("", 42)
    assert start == 42


def test_hunk_start_for_line_before_any_hunk_falls_back_to_line() -> None:
    """Line before the first hunk → falls back to line number."""
    start = hunk_start_for_line(SAMPLE_DIFF, 1)
    assert start == 1


def test_fingerprint_is_16_hex_chars() -> None:
    fp = fingerprint("src/foo.py", 10, SAMPLE_DIFF)
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)

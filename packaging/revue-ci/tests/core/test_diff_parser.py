#!/usr/bin/env python3
"""Tests for diff_parser — Story [001]."""

import pytest

from revue_core.core.diff_parser import (
    detect_language,
    filter_changes,
    parse_diff,
)
from revue_core.core.models import FileChange

# ---------------------------------------------------------------------------
# Fixtures: realistic unified diff strings
# ---------------------------------------------------------------------------

SINGLE_FILE_MODIFIED_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc1234..def5678 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,7 +10,8 @@ def run():
     config = load_config()
-    old_line = True
+    new_line = True
+    extra_line = True
     return config
"""

THREE_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 import os
+import sys

 def main():
diff --git a/src/utils.py b/src/utils.py
index 3333333..4444444 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -5,6 +5,7 @@ def helper():
     x = 1
+    y = 2
     return x
diff --git a/README.md b/README.md
index 5555555..6666666 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # Project
+Updated readme.
"""

NEW_FILE_DIFF = """\
diff --git a/src/new_module.py b/src/new_module.py
new file mode 100644
index 0000000..abcdef1
--- /dev/null
+++ b/src/new_module.py
@@ -0,0 +1,3 @@
+def hello():
+    return "hello"
+
"""

DELETED_FILE_DIFF = """\
diff --git a/src/old_module.py b/src/old_module.py
deleted file mode 100644
index abcdef1..0000000
--- a/src/old_module.py
+++ /dev/null
@@ -1,4 +1,0 @@
-def goodbye():
-    return "bye"
-
-# end
"""

BINARY_FILE_DIFF = """\
diff --git a/assets/logo.png b/assets/logo.png
index abc1234..def5678 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""


# ---------------------------------------------------------------------------
# Tests: parse_diff
# ---------------------------------------------------------------------------


class TestParseSingleFileModified:
    def test_parse_single_file_modified(self) -> None:
        changes = parse_diff(SINGLE_FILE_MODIFIED_DIFF)
        assert len(changes) == 1
        c = changes[0]
        assert c.file_path == "src/main.py"
        assert c.change_type == "modified"
        assert c.additions == 2
        assert c.deletions == 1


class TestParseMultipleFiles:
    def test_parse_multiple_files(self) -> None:
        changes = parse_diff(THREE_FILE_DIFF)
        assert len(changes) == 3
        paths = [c.file_path for c in changes]
        assert paths == ["src/app.py", "src/utils.py", "README.md"]


class TestParseNewFile:
    def test_parse_new_file(self) -> None:
        changes = parse_diff(NEW_FILE_DIFF)
        assert len(changes) == 1
        c = changes[0]
        assert c.change_type == "added"
        assert c.additions == 3
        assert c.deletions == 0


class TestParseDeletedFile:
    def test_parse_deleted_file(self) -> None:
        changes = parse_diff(DELETED_FILE_DIFF)
        assert len(changes) == 1
        c = changes[0]
        assert c.change_type == "deleted"
        assert c.deletions == 4
        assert c.additions == 0


class TestParseBinaryFile:
    def test_parse_binary_file(self) -> None:
        changes = parse_diff(BINARY_FILE_DIFF)
        assert len(changes) == 1
        c = changes[0]
        assert c.change_type == "binary"
        assert c.additions == 0
        assert c.deletions == 0
        assert c.diff == "[binary]"


class TestParseStripsBPrefix:
    def test_parse_strips_b_prefix(self) -> None:
        changes = parse_diff(SINGLE_FILE_MODIFIED_DIFF)
        assert changes[0].file_path == "src/main.py"
        assert not changes[0].file_path.startswith("b/")


# ---------------------------------------------------------------------------
# Tests: detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguagePython:
    def test_detect_language_python(self) -> None:
        assert detect_language("src/main.py") == "python"


class TestDetectLanguageTypescript:
    def test_detect_language_typescript(self) -> None:
        assert detect_language("components/App.tsx") == "typescript"


class TestDetectLanguageUnknown:
    def test_detect_language_unknown(self) -> None:
        assert detect_language("data/file.xyz") == "unknown"


# ---------------------------------------------------------------------------
# Tests: filter_changes
# ---------------------------------------------------------------------------


def _make_change(path: str, additions: int = 10, deletions: int = 5) -> FileChange:
    return FileChange(
        file_path=path,
        change_type="modified",
        additions=additions,
        deletions=deletions,
        diff="",
    )


class TestFilterChangesIgnorePattern:
    def test_filter_changes_ignore_pattern(self) -> None:
        changes = [
            _make_change("src/main.py"),
            _make_change("README.md"),
            _make_change("docs/guide.md"),
        ]
        included, excluded = filter_changes(changes, ignore_patterns=["*.md"])
        assert len(included) == 1
        assert included[0].file_path == "src/main.py"
        assert len(excluded) == 2


class TestFilterChangesMaxLines:
    def test_filter_changes_max_lines(self) -> None:
        changes = [
            _make_change("small.py", additions=10, deletions=5),
            _make_change("huge.py", additions=1500, deletions=502),
        ]
        included, excluded = filter_changes(changes, ignore_patterns=[], max_lines_changed=2000)
        assert len(included) == 1
        assert included[0].file_path == "small.py"
        assert len(excluded) == 1
        assert excluded[0].file_path == "huge.py"


class TestFilterChangesReturnsBothTuples:
    def test_filter_changes_returns_both_tuples(self) -> None:
        changes = [
            _make_change("keep.py", additions=5, deletions=5),
            _make_change("skip.md", additions=5, deletions=5),
            _make_change("big.py", additions=1500, deletions=600),
        ]
        included, excluded = filter_changes(
            changes, ignore_patterns=["*.md"], max_lines_changed=2000
        )
        # All input files accounted for
        assert len(included) + len(excluded) == len(changes)
        included_paths = {c.file_path for c in included}
        excluded_paths = {c.file_path for c in excluded}
        assert included_paths == {"keep.py"}
        assert excluded_paths == {"skip.md", "big.py"}


# ---------------------------------------------------------------------------
# Tests: language field populated on FileChange (AC: detect language from ext)
# ---------------------------------------------------------------------------

class TestLanguageDetectedOnParse:
    def test_language_populated_for_python_file(self) -> None:
        changes = parse_diff(SINGLE_FILE_MODIFIED_DIFF)
        assert changes[0].language == "python"

    def test_language_populated_for_markdown_file(self) -> None:
        changes = parse_diff(THREE_FILE_DIFF)
        md_change = next(c for c in changes if c.file_path == "README.md")
        assert md_change.language == "markdown"

    def test_language_populated_for_binary_file(self) -> None:
        changes = parse_diff(BINARY_FILE_DIFF)
        assert changes[0].language == "unknown"  # .png not in registry

    def test_language_unknown_for_unrecognised_extension(self) -> None:
        diff = """\
diff --git a/data/config.toml b/data/config.toml
index abc1234..def5678 100644
--- a/data/config.toml
+++ b/data/config.toml
@@ -1,2 +1,3 @@
 [section]
+key = "value"
"""
        changes = parse_diff(diff)
        assert changes[0].language == "unknown"


# ---------------------------------------------------------------------------
# Tests: edge cases — empty diff, binary, rename, >10 files (AC requirement)
# ---------------------------------------------------------------------------

class TestEmptyDiff:
    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_diff("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert parse_diff("   \n\n  ") == []

    def test_no_diff_header_returns_empty_list(self) -> None:
        assert parse_diff("just some random text\nno diff here") == []


class TestBinaryFileHandled:
    def test_binary_file_skipped_with_correct_fields(self) -> None:
        changes = parse_diff(BINARY_FILE_DIFF)
        assert len(changes) == 1
        c = changes[0]
        assert c.file_path == "assets/logo.png"
        assert c.change_type == "binary"
        assert c.additions == 0
        assert c.deletions == 0
        assert c.diff == "[binary]"

    def test_binary_in_multi_file_diff_does_not_block_others(self) -> None:
        diff = BINARY_FILE_DIFF + "\n" + SINGLE_FILE_MODIFIED_DIFF
        changes = parse_diff(diff)
        assert len(changes) == 2
        types = {c.change_type for c in changes}
        assert "binary" in types
        assert "modified" in types


class TestRenamedFile:
    def test_renamed_file_uses_new_path(self) -> None:
        diff = """\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 95%
rename from src/old_name.py
rename to src/new_name.py
index abc1234..def5678 100644
--- a/src/old_name.py
+++ b/src/new_name.py
@@ -1,3 +1,4 @@
 def hello():
+    # renamed
     return "hello"
"""
        changes = parse_diff(diff)
        assert len(changes) == 1
        assert changes[0].file_path == "src/new_name.py"
        assert changes[0].change_type == "modified"

    def test_renamed_file_language_from_new_path(self) -> None:
        diff = """\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 95%
rename from src/old_name.py
rename to src/new_name.py
index abc1234..def5678 100644
--- a/src/old_name.py
+++ b/src/new_name.py
@@ -1,3 +1,4 @@
 def hello():
+    # renamed
     return "hello"
"""
        changes = parse_diff(diff)
        assert changes[0].language == "python"


class TestMoreThanTenFiles:
    def test_parse_twelve_file_diff(self) -> None:
        """parse_diff handles diffs with >10 files correctly."""
        parts = []
        for i in range(12):
            parts.append(f"""\
diff --git a/src/module_{i}.py b/src/module_{i}.py
index abc{i:04d}..def{i:04d} 100644
--- a/src/module_{i}.py
+++ b/src/module_{i}.py
@@ -1,2 +1,3 @@
 x = {i}
+y = {i + 1}
""")
        changes = parse_diff("\n".join(parts))
        assert len(changes) == 12
        for i, c in enumerate(changes):
            assert c.file_path == f"src/module_{i}.py"
            assert c.additions == 1
            assert c.language == "python"

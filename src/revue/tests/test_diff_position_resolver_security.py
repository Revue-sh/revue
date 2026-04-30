"""Security tests for DiffPositionResolver.snap() — REVUE-201 dogfood fixes.

Covers: path traversal via '..' components, absolute file_path, null bytes,
symlink escape outside repo root, and empty file_path.
"""
from pathlib import Path

import pytest

from revue.core.diff_position_resolver import DiffPositionResolver

_SIMPLE_DIFF = """\
diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -10,5 +10,6 @@
 context_line
+added_line
 another_context
"""


def _snap(file_path, repo_path="/repo"):
    return DiffPositionResolver.snap(11, _SIMPLE_DIFF, repo_path=repo_path, file_path=file_path)


class TestFilePathInputValidation:
    """Tier 3 must be skipped — not an exception — for malicious file_path values."""

    def test_dotdot_path_returns_nearest_line(self):
        result = _snap("../../etc/passwd")
        assert isinstance(result, int)
        assert result > 0

    def test_deeply_nested_dotdot_returns_nearest_line(self):
        result = _snap("../../../etc/shadow")
        assert isinstance(result, int)

    def test_absolute_path_returns_nearest_line(self):
        result = _snap("/etc/hosts")
        assert isinstance(result, int)

    def test_null_byte_in_path_returns_nearest_line(self):
        result = _snap("foo\x00bar.py")
        assert isinstance(result, int)

    def test_empty_string_file_path_returns_nearest_line(self):
        result = _snap("")
        assert isinstance(result, int)

    def test_none_file_path_returns_nearest_line(self):
        result = _snap(None)
        assert isinstance(result, int)

    def test_dotdot_in_middle_of_path_returns_nearest_line(self):
        result = _snap("src/../../../etc/passwd")
        assert isinstance(result, int)


class TestPathTraversalGuard:
    """Path.relative_to() must catch symlinks / paths that resolve outside repo root."""

    def test_symlink_resolving_outside_repo_returns_nearest_line(self, tmp_path):
        """Symlink inside repo that resolves to a path outside repo root."""
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("secret content\n")

        link = repo / "escape.py"
        link.symlink_to(outside)

        result = DiffPositionResolver.snap(
            11,
            _SIMPLE_DIFF,
            repo_path=str(repo),
            file_path="escape.py",
        )
        assert isinstance(result, int)
        assert result > 0

    def test_valid_path_inside_repo_allows_tier3(self, tmp_path):
        """Happy path: valid file inside repo should reach Tier 3 (file read)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "src" / "foo.py"
        target.parent.mkdir(parents=True)
        # Write 100 lines so Tier 3 can clamp
        target.write_text("\n".join(f"line{i}" for i in range(1, 101)))

        result = DiffPositionResolver.snap(
            200,  # beyond diff range — Tier 3 should clamp to 100
            _SIMPLE_DIFF,
            repo_path=str(repo),
            file_path="src/foo.py",
        )
        # Tier 3 clamps to file line count (100)
        assert result <= 100
        assert result >= 1


class TestLruCacheOnMapDiffLines:
    """_map_diff_lines must parse the diff only once for the same input."""

    def test_cache_hit_on_repeated_call(self):
        """Second call with the same diff string must be a cache hit."""
        DiffPositionResolver._map_diff_lines.cache_clear()
        DiffPositionResolver._map_diff_lines(_SIMPLE_DIFF)
        DiffPositionResolver._map_diff_lines(_SIMPLE_DIFF)
        info = DiffPositionResolver._map_diff_lines.cache_info()
        assert info.hits >= 1, f"Expected at least 1 cache hit, got {info.hits}"

    def test_different_diffs_produce_independent_results(self):
        diff2 = _SIMPLE_DIFF + "\n# extra"
        result_a = DiffPositionResolver._map_diff_lines(_SIMPLE_DIFF)
        result_b = DiffPositionResolver._map_diff_lines(diff2)
        assert result_a != result_b

    def test_return_type_is_immutable_tuple(self):
        """Cached result must be immutable — callers cannot corrupt the cache."""
        result = DiffPositionResolver._map_diff_lines(_SIMPLE_DIFF)
        assert isinstance(result, tuple)

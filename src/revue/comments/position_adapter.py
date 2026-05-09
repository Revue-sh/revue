"""Per-platform position adapters for accurate inline comment anchoring (REVUE-236).

Implements a strict binary changed-line rule — no snapping, no proximity heuristics:
  - Reported line maps to a '+' line  → PlatformPosition (anchored)
  - Reported line is context or '-'   → None (routes to summary_sink)
  - Reported line not in diff at all  → None (routes to summary_sink)

Truncation exception: pure-addition hunks (@@ -0,0 +N,M @@) infer anchor from
the hunk header when the platform API truncated the body before the reported line.
This solves paginated/long-diff scenarios without being a proximity heuristic.

Usage:
    adapter = get_position_adapter("github", pr_context)
    pp = adapter.resolve(reported_line, diff_content, file_path, pr_context, rlc)
    if pp is None:
        # route finding to summary_sink
    else:
        params = adapter.to_api_params(pp)   # ready for VCS API call
"""
from __future__ import annotations

import re
from typing import Any, Protocol, TYPE_CHECKING, TypedDict

from revue.core.logging_channels import Log

if TYPE_CHECKING:
    from revue.core.models import PRContext

# Matches unified diff hunk headers: @@ -old[,count] +new[,count] @@
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PlatformPosition(TypedDict):
    """Anchored inline comment position, platform-agnostic."""
    file_path: str
    start_line: int   # 1-indexed new-file line number of anchor start
    end_line: int     # == start_line for single-line; start_line + rlc - 1 for multi-line


class PositionAdapter(Protocol):
    """Contract all platform adapters must satisfy."""

    def resolve(
        self,
        reported_line: int,
        diff: str,
        file_path: str,
        pr_context: "PRContext | None",
        replacement_line_count: int = 1,
    ) -> "PlatformPosition | None": ...

    def to_api_params(self, position: "PlatformPosition") -> dict: ...


# ---------------------------------------------------------------------------
# Diff parser (promoted from scripts/positioning/calculator.py)
# ---------------------------------------------------------------------------

def _parse_diff(
    diff_snippet: str,
) -> tuple[set[int], set[int], set[int], list[tuple[int, int, int, int]]]:
    """Parse unified diff into line-number sets and hunk ranges.

    Returns:
        plus_new    — new-file line numbers of '+' (added) lines
        context_new — new-file line numbers of ' ' (context) lines
        minus_old   — old-file line numbers of '-' (removed) lines
        hunks       — [(old_start, old_count, new_start, new_count), ...]
    """
    plus_new: set[int] = set()
    context_new: set[int] = set()
    minus_old: set[int] = set()
    hunks: list[tuple[int, int, int, int]] = []

    cur_old = cur_new = 0

    for line in diff_snippet.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            cur_old = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            cur_new = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            hunks.append((cur_old, old_count, cur_new, new_count))
            continue

        if line.startswith(("+++", "---", "diff --git", "index ", "\\ ")):
            continue

        if cur_old == 0 and cur_new == 0:
            continue  # before first hunk

        if line.startswith("+"):
            plus_new.add(cur_new)
            cur_new += 1
        elif line.startswith("-"):
            minus_old.add(cur_old)
            cur_old += 1
        else:
            context_new.add(cur_new)
            cur_old += 1
            cur_new += 1

    return plus_new, context_new, minus_old, hunks


# ---------------------------------------------------------------------------
# Base adapter — shared resolve() logic
# ---------------------------------------------------------------------------

class _BasePositionAdapter:
    """Implements the shared changed-line rule; subclasses override to_api_params()."""

    def resolve(
        self,
        reported_line: int,
        diff: str,
        file_path: str,
        pr_context: "PRContext | None",
        replacement_line_count: int = 1,
    ) -> "PlatformPosition | None":
        adapter_name = type(self).__name__

        Log.position.info(
            "[pos] ═══ resolve START ═══  adapter=%s  file=%s  reported_line=%d  rlc=%d",
            adapter_name, file_path, reported_line, replacement_line_count,
        )
        Log.position.info(
            "[pos] diff_length=%d chars  diff_empty=%s",
            len(diff), diff.strip() == "",
        )
        if not diff.strip():
            Log.position.info(
                "[pos] ✗ UNANCHORED — diff is empty; reported_line=%d has no hunk to resolve against → summary_sink",
                reported_line,
            )
            return None

        plus_new, context_new, minus_old, hunks = _parse_diff(diff)

        Log.position.info(
            "[pos] diff parse result:  %d hunk(s)  |  %d added line(s)  |  %d context line(s)  |  %d removed line(s)",
            len(hunks), len(plus_new), len(context_new), len(minus_old),
        )

        if not hunks:
            Log.position.info("[pos] ✗ UNANCHORED — no hunks parsed from diff → summary_sink")
            return None

        for i, (old_start, old_count, new_start, new_count) in enumerate(hunks):
            new_end = new_start + new_count - 1
            old_end = old_start + old_count - 1
            Log.position.info(
                "[pos] hunk[%d]  @@ -%d,%d +%d,%d @@  "
                "old-file lines %d–%d  |  new-file lines %d–%d  |  pure-addition=%s",
                i, old_start, old_count, new_start, new_count,
                old_start, old_end, new_start, new_end,
                old_count == 0,
            )

        Log.position.info(
            "[pos] added (+) lines   : %s",
            sorted(plus_new) if plus_new else "(none)",
        )
        Log.position.info(
            "[pos] context ( ) lines : %s",
            sorted(context_new) if context_new else "(none)",
        )
        Log.position.info(
            "[pos] removed (-) lines : %s",
            sorted(minus_old) if minus_old else "(none)",
        )

        Log.position.info(
            "[pos] checking reported_line=%d  →  in plus_new=%s  in context_new=%s  in minus_old=%s",
            reported_line,
            reported_line in plus_new,
            reported_line in context_new,
            reported_line in minus_old,
        )

        if reported_line in plus_new:
            end_line = reported_line + replacement_line_count - 1
            pp = PlatformPosition(
                file_path=file_path,
                start_line=reported_line,
                end_line=end_line,
            )
            Log.position.info(
                "[pos] ✓ ANCHORED — line %d is a '+' (added) line  →  start=%d  end=%d  rlc=%d",
                reported_line, pp["start_line"], pp["end_line"], replacement_line_count,
            )
            return pp

        if reported_line in context_new:
            Log.position.info(
                "[pos] ✗ UNANCHORED — line %d is a context (' ') line; "
                "it exists in the new file but was not changed → summary_sink",
                reported_line,
            )
            return None

        if reported_line in minus_old:
            Log.position.info(
                "[pos] ✗ UNANCHORED — line %d is a removed ('-') line; "
                "it no longer exists in the new file → summary_sink",
                reported_line,
            )
            return None

        # Truncation fallback: pure-addition hunk (@@ -0,0 +N,M @@) — infer anchor
        # from hunk header when the diff body was truncated before this line.
        Log.position.info(
            "[pos] line %d not found in any parsed set — checking truncation fallback "
            "(pure-addition hunks only)",
            reported_line,
        )
        for i, (old_start, old_count, new_start, new_count) in enumerate(hunks):
            is_pure_add = old_count == 0
            new_end = new_start + new_count - 1
            in_range = new_start <= reported_line <= new_end
            Log.position.info(
                "[pos] truncation check hunk[%d]  pure-addition=%s  "
                "range=[%d–%d]  reported_line=%d  in_range=%s",
                i, is_pure_add, new_start, new_end, reported_line, in_range,
            )
            if is_pure_add and in_range:
                end_line = reported_line + replacement_line_count - 1
                pp = PlatformPosition(
                    file_path=file_path,
                    start_line=reported_line,
                    end_line=end_line,
                )
                Log.position.info(
                    "[pos] ✓ ANCHORED (truncation fallback) — line %d inferred from "
                    "@@ -0,0 +%d,%d @@ header  →  start=%d  end=%d  rlc=%d",
                    reported_line, new_start, new_count,
                    pp["start_line"], pp["end_line"], replacement_line_count,
                )
                return pp

        Log.position.info(
            "[pos] ✗ UNANCHORED — line %d not in any hunk (plus/context/minus/truncation); "
            "diff covers new-file lines %s → summary_sink",
            reported_line,
            sorted(plus_new | context_new) if (plus_new | context_new) else "(none)",
        )
        return None

    def to_api_params(self, position: "PlatformPosition") -> dict:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Platform adapters
# ---------------------------------------------------------------------------

class GitHubPositionAdapter(_BasePositionAdapter):
    """GitHub pull request review comment params.

    Single-line: {"path": ..., "side": "RIGHT", "line": end_line}
    Multi-line:  adds "start_line" and "start_side" when end_line != start_line
    """

    def to_api_params(self, position: "PlatformPosition") -> dict:
        params: dict[str, Any] = {
            "path": position["file_path"],
            "side": "RIGHT",
            "line": position["end_line"],
        }
        if position["end_line"] != position["start_line"]:
            params["start_line"] = position["start_line"]
            params["start_side"] = "RIGHT"
        return params


class GitLabPositionAdapter(_BasePositionAdapter):
    """GitLab merge request discussion position params.

    SHAs are fetched once by get_position_adapter() at construction time,
    not per comment. Multi-line ranges are expressed in the comment body
    via suggestion:-0+K fences, not in the position object.
    """

    def __init__(self, base_sha: str, head_sha: str, start_sha: str) -> None:
        self._base_sha = base_sha
        self._head_sha = head_sha
        self._start_sha = start_sha

    def to_api_params(self, position: "PlatformPosition") -> dict:
        return {
            "position_type": "text",
            "base_sha": self._base_sha,
            "head_sha": self._head_sha,
            "start_sha": self._start_sha,
            "new_path": position["file_path"],
            "old_path": position["file_path"],
            "new_line": position["start_line"],
        }


# ---------------------------------------------------------------------------
# Factory — OCP: add a registry entry to support a new platform; no code edits
# ---------------------------------------------------------------------------

def _make_github(pr_context: "PRContext | None", vcs_adapter: Any) -> GitHubPositionAdapter:
    return GitHubPositionAdapter()


def _make_gitlab(pr_context: "PRContext | None", vcs_adapter: Any) -> GitLabPositionAdapter:
    base_sha = head_sha = start_sha = ""
    if vcs_adapter is not None and hasattr(vcs_adapter, "_get_mr_version_shas"):
        pr_id = pr_context.pr_number if pr_context is not None else 0
        base_sha, start_sha, head_sha = vcs_adapter._get_mr_version_shas(pr_id)
    return GitLabPositionAdapter(base_sha=base_sha, head_sha=head_sha, start_sha=start_sha)


_ADAPTER_FACTORY_REGISTRY: dict[str, Any] = {
    "github": _make_github,
    "gitlab": _make_gitlab,
    # "bitbucket": _make_bitbucket  ← REVUE-238 follow-up
}


def get_position_adapter(
    platform: str,
    pr_context: "PRContext | None",
    vcs_adapter: Any = None,
) -> PositionAdapter:
    """Return the PositionAdapter for *platform*, pre-loaded with any required context.

    For GitLab, *vcs_adapter* must be a GitLabAdapter instance so the factory
    can fetch MR version SHAs. If None, the GitLabPositionAdapter is returned
    with empty SHAs (suitable for unit-test use).

    Raises KeyError for unknown platforms.
    """
    factory = _ADAPTER_FACTORY_REGISTRY[platform]
    return factory(pr_context, vcs_adapter)

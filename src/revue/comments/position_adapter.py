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
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TYPE_CHECKING, TypedDict

from revue.core.logging_channels import Log

if TYPE_CHECKING:
    from revue.core.models import PRContext

# Matches unified diff hunk headers: @@ -old[,count] +new[,count] @@
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PlatformPosition(TypedDict):
    """Inline comment position with classification result, platform-agnostic.

    resolve() always returns a PlatformPosition — status replaces the old None sentinel.
    start_line and end_line are set only when status == ANCHORED; None otherwise.
    Nova routes by status: anchored → post inline; context_line → post with note;
    removed_line → flag stale; out_of_hunk → post to summary.
    """
    file_path: str
    status: "PositionStatus"
    reason: str
    start_line: "int | None"  # 1-indexed new-file line; None when not ANCHORED
    end_line: "int | None"    # start_line + rlc - 1; None when not ANCHORED


class PositionAdapter(Protocol):
    """Contract all platform adapters must satisfy."""

    def resolve(
        self,
        reported_line: int,
        diff: str,
        file_path: str,
        pr_context: "PRContext | None",
        replacement_line_count: int = 1,
    ) -> "PlatformPosition": ...

    def to_api_params(self, position: "PlatformPosition") -> dict: ...


# ---------------------------------------------------------------------------
# Diff parser (promoted from scripts/positioning/calculator.py)
# ---------------------------------------------------------------------------

@dataclass
class ParsedDiff:
    """Line-number sets and hunk ranges extracted from a unified diff snippet.

    Coordinate systems differ by field — mixing them is a positioning bug:
      plus_new, context_new  → absolute new-file line numbers (post-patch)
      minus_old              → absolute old-file line numbers (pre-patch)

    Attributes:
        plus_new:    New-file line numbers of '+' (added) lines.
        context_new: New-file line numbers of ' ' (context/unchanged) lines.
        minus_old:   Old-file line numbers of '-' (removed) lines.
        hunks:       Parsed hunk headers as (old_start, old_count, new_start, new_count),
                     matching @@ -old_start,old_count +new_start,new_count @@ syntax.
    """
    plus_new: set[int]
    context_new: set[int]
    minus_old: set[int]
    hunks: list[tuple[int, int, int, int]]


def _parse_diff(diff_snippet: str) -> ParsedDiff:
    """Parse unified diff into line-number sets and hunk ranges."""
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

    return ParsedDiff(plus_new=plus_new, context_new=context_new, minus_old=minus_old, hunks=hunks)


# ---------------------------------------------------------------------------
# Public classification API — pure, no logging
# ---------------------------------------------------------------------------

class PositionStatus(str, Enum):
    """Classification of a reported line within a unified diff."""
    ANCHORED = "anchored"
    CONTEXT_LINE = "context_line"
    REMOVED_LINE = "removed_line"
    OUT_OF_HUNK = "out_of_hunk"


@dataclass
class PositionResult:
    """Classification result from calculate(). Pure data — no platform specifics."""
    file_path: str
    start_line: int | None
    end_line: int | None
    status: PositionStatus
    reason: str


def calculate(
    diff_snippet: str,
    reported_line: int,
    file_path: str,
    replacement_line_count: int = 1,
) -> PositionResult:
    """Classify *reported_line* within *diff_snippet*. Pure function — no side effects.

    Status values:
      anchored      — '+' line (or inferred from pure-addition hunk header)
      context_line  — space-prefixed, unchanged line
      removed_line  — '-' line, no longer in the new file
      out_of_hunk   — line not present in any hunk
    """
    parsed = _parse_diff(diff_snippet)

    if reported_line in parsed.plus_new:
        return PositionResult(
            file_path=file_path,
            start_line=reported_line,
            end_line=reported_line + replacement_line_count - 1,
            status=PositionStatus.ANCHORED,
            reason=f"line {reported_line} is a '+' line in the diff",
        )
    if reported_line in parsed.context_new:
        return PositionResult(
            file_path=file_path, start_line=None, end_line=None,
            status=PositionStatus.CONTEXT_LINE,
            reason=f"line {reported_line} is a context (' ') line — not anchorable",
        )
    if reported_line in parsed.minus_old:
        return PositionResult(
            file_path=file_path, start_line=None, end_line=None,
            status=PositionStatus.REMOVED_LINE,
            reason=f"line {reported_line} is a removed ('-') line in the old file",
        )
    for old_start, old_count, new_start, new_count in parsed.hunks:
        if old_count == 0 and new_start <= reported_line <= new_start + new_count - 1:
            return PositionResult(
                file_path=file_path,
                start_line=reported_line,
                end_line=reported_line + replacement_line_count - 1,
                status=PositionStatus.ANCHORED,
                reason=(
                    f"line {reported_line} inferred as '+' from pure-addition hunk "
                    f"@@ -{old_start},{old_count} +{new_start},{new_count} @@ "
                    f"(snippet truncated before this line)"
                ),
            )
    return PositionResult(
        file_path=file_path, start_line=None, end_line=None,
        status=PositionStatus.OUT_OF_HUNK,
        reason=f"line {reported_line} does not appear in any diff hunk",
    )


# ---------------------------------------------------------------------------
# Base adapter — shared resolve() logic
# ---------------------------------------------------------------------------

class _BasePositionAdapter:
    """Implements the shared changed-line rule; subclasses override to_api_params()."""

    @staticmethod
    def _validate_anchored(position: "PlatformPosition") -> None:
        """Precondition shared by all to_api_params() implementations.

        A PlatformPosition passed to to_api_params() must be ANCHORED with a
        non-None, non-inverted line range. Each subclass calls this first.
        """
        assert position["status"] == PositionStatus.ANCHORED, (
            f"to_api_params requires ANCHORED status, got {position['status']}"
        )
        assert position["start_line"] is not None and position["end_line"] is not None, (
            f"start_line and end_line must be non-None when ANCHORED, got "
            f"start={position['start_line']} end={position['end_line']}"
        )
        assert position["end_line"] >= position["start_line"], (
            f"Invalid range: end_line={position['end_line']} < start_line={position['start_line']}"
        )

    def resolve(
        self,
        reported_line: int,
        diff: str,
        file_path: str,
        pr_context: "PRContext | None",
        replacement_line_count: int = 1,
    ) -> "PlatformPosition":
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
                "[pos] ✗ UNANCHORED — diff is empty; reported_line=%d → summary_sink",
                reported_line,
            )
            return PlatformPosition(
                file_path=file_path,
                status=PositionStatus.OUT_OF_HUNK,
                reason="diff is empty — no hunks to resolve against",
                start_line=None,
                end_line=None,
            )

        result = calculate(diff, reported_line, file_path, replacement_line_count)

        Log.position.info(
            "[pos] classify → status=%s  reason=%s",
            result.status.value, result.reason,
        )

        if result.status == PositionStatus.ANCHORED:
            assert result.start_line is not None and result.end_line is not None
            pp = PlatformPosition(
                file_path=file_path,
                status=PositionStatus.ANCHORED,
                reason=result.reason,
                start_line=result.start_line,
                end_line=result.end_line,
            )
            Log.position.info(
                "[pos] ✓ ANCHORED  →  start=%d  end=%d  rlc=%d",
                pp["start_line"], pp["end_line"], replacement_line_count,
            )
            return pp

        Log.position.info("[pos] ✗ UNANCHORED → summary_sink")
        return PlatformPosition(
            file_path=file_path,
            status=result.status,
            reason=result.reason,
            start_line=None,
            end_line=None,
        )

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
        self._validate_anchored(position)
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
        self._validate_anchored(position)
        return {
            "position_type": "text",
            "base_sha": self._base_sha,
            "head_sha": self._head_sha,
            "start_sha": self._start_sha,
            "new_path": position["file_path"],
            "old_path": position["file_path"],
            "new_line": position["start_line"],
        }


class BitbucketPositionAdapter(_BasePositionAdapter):
    """Bitbucket pull request inline comment params.

    Single-line: {"inline": {"path": ..., "to": end_line}}
    Multi-line:  adds "from": start_line when end_line != start_line
    """

    def to_api_params(self, position: "PlatformPosition") -> dict:
        self._validate_anchored(position)
        inline: dict[str, Any] = {"path": position["file_path"], "to": position["end_line"]}
        if position["end_line"] != position["start_line"]:
            inline["from"] = position["start_line"]
        return {"inline": inline}


# ---------------------------------------------------------------------------
# Factory — OCP: add a registry entry to support a new platform; no code edits
# ---------------------------------------------------------------------------

def _stateless_factory(cls: type) -> Any:
    """Wrap a stateless adapter class as a factory function.

    Stateless adapters (GitHub, Bitbucket) don't need pr_context or vcs_adapter;
    the factory wrapper accepts them only to satisfy the registry signature.
    """
    return lambda pr_context, vcs_adapter: cls()


def _make_gitlab(pr_context: "PRContext | None", vcs_adapter: Any) -> GitLabPositionAdapter:
    base_sha = head_sha = start_sha = ""
    if vcs_adapter is not None and hasattr(vcs_adapter, "_get_mr_version_shas"):
        pr_id = pr_context.pr_number if pr_context is not None else 0
        base_sha, start_sha, head_sha = vcs_adapter._get_mr_version_shas(pr_id)
    return GitLabPositionAdapter(base_sha=base_sha, head_sha=head_sha, start_sha=start_sha)


_ADAPTER_FACTORY_REGISTRY: dict[str, Any] = {
    "github": _stateless_factory(GitHubPositionAdapter),
    "gitlab": _make_gitlab,
    "bitbucket": _stateless_factory(BitbucketPositionAdapter),
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

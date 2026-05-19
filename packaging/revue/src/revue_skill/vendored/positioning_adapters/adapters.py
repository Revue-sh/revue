from __future__ import annotations

from typing import Protocol

from revue_skill.vendored.position_adapter import PositionResult, PositionStatus


class PositionAdapter(Protocol):
    """Converts a PositionResult into the platform-specific API params dict."""

    def build_params(self, result: PositionResult, fixture_meta: dict) -> dict | None:
        """Return the platform API payload for posting an inline comment, or None
        when the position is not anchorable."""
        ...


class GitHubPositionAdapter:
    """Builds GitHub pull request review comment params.

    Single-line: {"path": ..., "side": "RIGHT", "line": end_line}
    Multi-line:  adds "start_line" and "start_side" when end_line != start_line
    """

    def build_params(self, result: PositionResult, fixture_meta: dict) -> dict | None:
        if result.status != PositionStatus.ANCHORED:
            return None
        params: dict = {
            "path": result.file_path,
            "side": "RIGHT",
            "line": result.end_line,
        }
        if result.end_line != result.start_line:
            params["start_line"] = result.start_line
            params["start_side"] = "RIGHT"
        return params


class GitLabPositionAdapter:
    """Builds GitLab merge request discussion position params.

    SHAs are read from fixture_meta (posted_base_sha, posted_head_sha, posted_start_sha)
    since they come from the live MR, not from the diff snippet.
    """

    def build_params(self, result: PositionResult, fixture_meta: dict) -> dict | None:
        if result.status != PositionStatus.ANCHORED:
            return None
        return {
            "position_type": "text",
            "base_sha": fixture_meta.get("posted_base_sha", ""),
            "head_sha": fixture_meta.get("posted_head_sha", ""),
            "start_sha": fixture_meta.get("posted_start_sha", ""),
            "new_path": result.file_path,
            "old_path": result.file_path,
            "new_line": result.start_line,
        }


class BitbucketPositionAdapter:
    """Builds Bitbucket pull request inline comment params.

    Single-line: {"inline": {"path": ..., "to": end_line}}
    Multi-line:  adds "from": start_line when end_line != start_line
    """

    def build_params(self, result: PositionResult, fixture_meta: dict) -> dict | None:
        if result.status != PositionStatus.ANCHORED:
            return None
        inline: dict = {"path": result.file_path, "to": result.end_line}
        if result.end_line != result.start_line:
            inline["from"] = result.start_line
        return {"inline": inline}


# Registry — all platform branching goes through here (OCP: add an entry, no code edits)
ADAPTERS: dict[str, PositionAdapter] = {
    "github": GitHubPositionAdapter(),
    "gitlab": GitLabPositionAdapter(),
    "bitbucket": BitbucketPositionAdapter(),
}

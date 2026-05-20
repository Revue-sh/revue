"""HunkTracker: prior-comment resolution state machine (REVUE-211).

Tracks what happened to each prior Revue comment across CI runs using sentinel
markers embedded in thread reply bodies. State is persisted in-thread (no DB
required) so it survives ephemeral CI checkouts.

State machine: 14 legal paths, 6 forbidden transitions enforced as guards.
See docs/architecture/comment-posting.md §State Machine for the full path map.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from revue_core.comments.models import HunkState, ResolutionResult, ResolutionVerdict
from revue_core.core.diff_position_resolver import DiffPositionResolver

if TYPE_CHECKING:
    pass

from revue_core.core.logging_channels import Log

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidStateTransitionError(Exception):
    """Raised when the state machine detects a forbidden transition."""


# ---------------------------------------------------------------------------
# Legal transition table
# ---------------------------------------------------------------------------

_LEGAL_TRANSITIONS: frozenset[tuple[HunkState, HunkState]] = frozenset({
    (HunkState.INITIAL, HunkState.PLATFORM_RESOLVED),
    (HunkState.INITIAL, HunkState.AUTO_RESOLVED),
    (HunkState.INITIAL, HunkState.UNTOUCHED),
    (HunkState.INITIAL, HunkState.CODE_REMOVED),
    (HunkState.INITIAL, HunkState.CHANGED),
    # FOLLOW_UP_POSTED prior → same transitions as INITIAL
    (HunkState.FOLLOW_UP_POSTED, HunkState.UNTOUCHED),
    (HunkState.FOLLOW_UP_POSTED, HunkState.CODE_REMOVED),
    (HunkState.FOLLOW_UP_POSTED, HunkState.CHANGED),
    # Downstream transitions
    (HunkState.CODE_REMOVED, HunkState.RESOLVE_REPLY_POSTED),
    (HunkState.CODE_REMOVED, HunkState.REPLY_FAILED),
    (HunkState.CHANGED, HunkState.NOVA_CALLED),
    (HunkState.NOVA_CALLED, HunkState.FULLY_ADDRESSED),
    (HunkState.NOVA_CALLED, HunkState.NOT_FULLY_ADDRESSED),
    (HunkState.NOVA_CALLED, HunkState.NOVA_ERROR),
    (HunkState.FULLY_ADDRESSED, HunkState.RESOLVE_REPLY_POSTED),
    (HunkState.FULLY_ADDRESSED, HunkState.REPLY_FAILED),
    (HunkState.NOT_FULLY_ADDRESSED, HunkState.FOLLOW_UP_POSTED),
    (HunkState.NOT_FULLY_ADDRESSED, HunkState.REPLY_FAILED),
})

_TERMINAL_STATES: frozenset[HunkState] = frozenset({
    HunkState.AUTO_RESOLVED,
    HunkState.PLATFORM_RESOLVED,
})

# ---------------------------------------------------------------------------
# Sentinel format
# ---------------------------------------------------------------------------

_SENTINEL_RE = re.compile(
    r"\[//\]: # \(revue:state=([\w]+):fp=([a-f0-9]{16}):ts=([^ )]+?)\)"
)

_FOLLOW_UP_REPLY_TEMPLATE = (
    "⚠️ This finding is not yet fully resolved.\n\n"
    "{guidance}\n\n"
    "{sentinel}"
)

_RESOLVE_REPLY_TEMPLATE = (
    "✅ {message}\n\n{sentinel}"
)


# ---------------------------------------------------------------------------
# HunkTracker
# ---------------------------------------------------------------------------


class HunkTracker:
    """Prior-comment state machine + sentinel persistence for REVUE-211.

    Depends only on:
    - VCSAdapter (Protocol) for get_thread_replies() and resolve_inline_comment()
    - PerPRCommentStore for get_unresolved_fingerprints()
    - ResolutionStrategy (Protocol) for semantic "is it fixed?" analysis
    """

    def __init__(self, adapter, dedup_store, resolution_strategy) -> None:
        self._adapter = adapter
        self._dedup_store = dedup_store
        self._resolution_strategy = resolution_strategy

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build_prior(self, platform_str: str, pr_num: int) -> dict[str, dict]:
        """Merge dedup store + live API scan + sentinel reconstruction.

        Returns {fingerprint: entry_dict} where each entry may include
        a 'sentinel_state' key if a Revue sentinel was found in the thread.

        Local dedup store entries take precedence over API-scanned entries
        (richer metadata), but sentinel_state from API scans is merged in.
        """
        prior_unresolved = self._dedup_store.get_unresolved_fingerprints(platform_str, pr_num)
        Log.pipeline.debug("[HunkTracker] build_prior: platform=%s pr_num=%s dedup_store has %d entries", platform_str, pr_num, len(prior_unresolved))
        api_fps = self._build_api_fingerprint_map(pr_num)
        Log.pipeline.debug("[HunkTracker] build_prior: api_fingerprint_map returned %d fingerprints", len(api_fps))

        # Merge: local store takes precedence for base fields, but sentinel_state
        # must come from the most recent source (compare sentinel_ts timestamps).
        merged: dict[str, dict] = {**api_fps}
        for fp, local_entry in prior_unresolved.items():
            local = dict(local_entry)
            if fp in merged:
                api_entry = merged[fp]
                api_ts = api_entry.get("sentinel_ts", "")
                local_ts = local.get("sentinel_ts", "")
                # Use whichever has the more recent sentinel; local fields take
                # precedence for non-sentinel metadata (file_path, line_number, etc.).
                merged[fp] = local
                if api_ts and (not local_ts or api_ts > local_ts):
                    merged[fp]["sentinel_state"] = api_entry.get("sentinel_state", "")
                    merged[fp]["sentinel_ts"] = api_ts
                elif local_ts:
                    merged[fp].setdefault("sentinel_state", api_entry.get("sentinel_state", ""))
            else:
                merged[fp] = local

        Log.pipeline.debug("[HunkTracker] build_prior: merged result has %d entries", len(merged))
        return merged

    def resolution_status(
        self,
        fingerprint: str,
        prior_entry: dict,
        new_diff: str,
    ) -> HunkState:
        """Determine the current resolution state of a prior finding.

        ``prior_entry`` is one entry from build_prior(). ``new_diff`` is the
        per-file diff string for the file that contained the finding (empty
        string if the file was not in the current diff at all).

        Returns a HunkState. Never raises — errors are logged and returned
        as NOVA_ERROR or REPLY_FAILED as appropriate.
        """
        state = HunkState.INITIAL
        comment_id = prior_entry.get("platform_comment_id", "")
        line_number: int = prior_entry.get("line_number", 0)
        pr_num: int = prior_entry.get("pr_num", 0)
        Log.pipeline.debug("[HunkTracker] resolution_status: fp=%s comment_id=%s line_number=%s", fingerprint, comment_id, line_number)

        # --- Terminal-state checks (no transition possible) ---

        if prior_entry.get("resolved", False):
            self._transition(state, HunkState.PLATFORM_RESOLVED)
            Log.pipeline.debug("[HunkTracker] fp=%s → PLATFORM_RESOLVED (thread resolved on platform)", fingerprint)
            return HunkState.PLATFORM_RESOLVED

        sentinel_state = prior_entry.get("sentinel_state", "")
        if sentinel_state == "auto_resolved":
            self._transition(state, HunkState.AUTO_RESOLVED)
            Log.pipeline.debug("[HunkTracker] fp=%s → AUTO_RESOLVED (prior sentinel)", fingerprint)
            return HunkState.AUTO_RESOLVED

        # --- Three-way diff check ---

        prior_state = (
            HunkState.FOLLOW_UP_POSTED
            if sentinel_state == "follow_up_posted"
            else HunkState.INITIAL
        )

        if not new_diff:
            self._transition(prior_state, HunkState.UNTOUCHED)
            Log.pipeline.debug("[HunkTracker] fp=%s file not in diff → UNTOUCHED", fingerprint)
            return HunkState.UNTOUCHED

        # File is in diff. Check if the specific line still exists.
        if line_number == 0:
            # Location unknown (could not be extracted from API) — cannot determine resolution.
            self._transition(prior_state, HunkState.UNTOUCHED)
            Log.pipeline.debug("[HunkTracker] fp=%s line=0 unknown location → UNTOUCHED", fingerprint)
            return HunkState.UNTOUCHED

        line_present = DiffPositionResolver.line_in_diff(line_number, new_diff)

        if not line_present:
            # Lines were removed or file was restructured.
            self._transition(prior_state, HunkState.CODE_REMOVED)
            Log.pipeline.debug("[HunkTracker] fp=%s line=%s not in new diff → CODE_REMOVED", fingerprint, line_number)
            return self._auto_resolve(comment_id, pr_num, fingerprint, "The code containing this finding has been removed.")

        # Lines still present and modified → semantic check via Nova.
        self._transition(prior_state, HunkState.CHANGED)
        Log.pipeline.debug("[HunkTracker] fp=%s line=%s present in diff → CHANGED, calling Nova", fingerprint, line_number)

        return self._call_nova(comment_id, pr_num, fingerprint, prior_entry, new_diff, prior_state=HunkState.CHANGED)

    # ------------------------------------------------------------------
    # State machine guard
    # ------------------------------------------------------------------

    def _transition(self, from_state: HunkState, to_state: HunkState) -> None:
        """Assert that from_state → to_state is legal; raise otherwise."""
        Log.pipeline.debug("[HunkTracker] transition: %s → %s", from_state.value, to_state.value)
        if from_state in _TERMINAL_STATES:
            raise InvalidStateTransitionError(
                f"{from_state.value!r} is a terminal state — no transitions allowed"
            )
        if (from_state, to_state) not in _LEGAL_TRANSITIONS:
            raise InvalidStateTransitionError(
                f"Forbidden transition: {from_state.value!r} → {to_state.value!r}"
            )

    # ------------------------------------------------------------------
    # Sentinel helpers
    # ------------------------------------------------------------------

    def _parse_sentinel(self, body: str) -> dict | None:
        """Extract the Revue sentinel from a comment/reply body.

        Returns dict(state, fp, ts) or None if no sentinel found.
        """
        m = _SENTINEL_RE.search(body)
        if not m:
            return None
        sentinel = {"state": m.group(1), "fp": m.group(2), "ts": m.group(3)}
        Log.pipeline.debug("[HunkTracker] parsed sentinel: state=%s fp=%s ts=%s", sentinel["state"], sentinel["fp"], sentinel["ts"])
        return sentinel

    def _most_recent_sentinel(self, replies: list[dict]) -> dict | None:
        """Return the sentinel with the latest ts from a list of reply dicts.

        Each reply dict must have a 'body' key. Returns None if no sentinel found.
        """
        best: dict | None = None
        for reply in replies:
            sentinel = self._parse_sentinel(reply.get("body", ""))
            if sentinel:
                if best is None or sentinel["ts"] > best["ts"]:
                    best = sentinel
        return best

    def _make_sentinel(self, state: str, fp: str) -> str:
        if not re.match(r'^[a-f0-9]{16}$', fp):
            raise ValueError(f'Invalid fingerprint format: {fp!r}')
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"[//]: # (revue:state={state}:fp={fp}:ts={ts})"

    # ------------------------------------------------------------------
    # API scan helpers
    # ------------------------------------------------------------------

    def _build_api_fingerprint_map(self, pr_num: int) -> dict[str, dict]:
        """Scan live PR comments and thread replies for fingerprints and sentinels."""
        from revue_core.comments.fingerprint import fingerprint as gen_fingerprint

        result: dict[str, dict] = {}
        try:
            comments = self._adapter.get_existing_comments(pr_id=pr_num)
        except Exception as exc:
            Log.pipeline.warning("_build_api_fingerprint_map: get_existing_comments failed: %s", exc)
            return result

        Log.pipeline.debug("[HunkTracker] scanning %d comments for fingerprints/sentinels", len(comments))
        for c in comments:
            try:
                body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                effective_id = str(c.get("_discussion_id", "") or c.get("id", ""))
                resolved = bool(c.get("_discussion_resolved", False))

                # Fingerprint from existing sentinel or location strategy
                _apply_location_entry(c, body, effective_id, result, gen_fingerprint, resolved)

                # Check thread replies for the most recent Revue sentinel
                replies = self._adapter.get_thread_replies(pr_num, effective_id)
                reply_sentinel = self._most_recent_sentinel(replies)
                if reply_sentinel:
                    fp = reply_sentinel["fp"]
                    if fp not in result:
                        result[fp] = {
                            "platform_comment_id": effective_id,
                            "resolved": resolved,
                        }
                    if reply_sentinel["ts"] >= result[fp].get("sentinel_ts", ""):
                        result[fp]["sentinel_state"] = reply_sentinel["state"]
                        result[fp]["sentinel_ts"] = reply_sentinel["ts"]
            except Exception as exc:
                Log.pipeline.warning("_build_api_fingerprint_map: error processing comment %s: %s", c.get("id", "?"), exc)

        return result

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _auto_resolve(
        self, comment_id: str, pr_num: int, fingerprint: str, message: str
    ) -> HunkState:
        """Post resolve reply with auto_resolved sentinel and resolve thread."""
        Log.pipeline.debug("[HunkTracker] _auto_resolve: comment_id=%s fp=%s", comment_id, fingerprint)
        if not comment_id:
            self._transition(HunkState.CODE_REMOVED, HunkState.REPLY_FAILED)
            return HunkState.REPLY_FAILED

        sentinel = self._make_sentinel("auto_resolved", fingerprint)
        reply_body = _RESOLVE_REPLY_TEMPLATE.format(message=message, sentinel=sentinel)

        ok = self._adapter.resolve_inline_comment(
            pr_id=pr_num, comment_id=comment_id, reply_body=reply_body
        )
        Log.pipeline.debug("[HunkTracker] _auto_resolve: resolve_inline_comment ok=%s", ok)
        if ok:
            self._transition(HunkState.CODE_REMOVED, HunkState.RESOLVE_REPLY_POSTED)
            return HunkState.RESOLVE_REPLY_POSTED
        self._transition(HunkState.CODE_REMOVED, HunkState.REPLY_FAILED)
        return HunkState.REPLY_FAILED

    def _call_nova(
        self,
        comment_id: str,
        pr_num: int,
        fingerprint: str,
        prior_entry: dict,
        new_diff: str,
        prior_state: HunkState = HunkState.CHANGED,
    ) -> HunkState:
        """Call ResolutionStrategy then handle result."""
        Log.pipeline.debug("[HunkTracker] _call_nova: fp=%s", fingerprint)
        prior_follow_up = prior_entry.get("prior_follow_up_body")
        original_finding = {
            "issue": prior_entry.get("comment_body", ""),
            "code": prior_entry.get("snippet", ""),
        }

        self._transition(prior_state, HunkState.NOVA_CALLED)

        try:
            result = self._resolution_strategy.resolve(
                original_finding=original_finding,
                new_hunk=new_diff,
                prior_follow_up=prior_follow_up,
            )
        except Exception as exc:
            Log.pipeline.warning("ResolutionStrategy.resolve failed (fp=%s): %s", fingerprint, exc)
            self._transition(HunkState.NOVA_CALLED, HunkState.NOVA_ERROR)
            return HunkState.NOVA_ERROR

        Log.pipeline.debug("[HunkTracker] nova verdict: %s guidance=%r", result.verdict.value, result.guidance[:60] if result.guidance else "")
        if result.verdict == ResolutionVerdict.FULLY:
            self._transition(HunkState.NOVA_CALLED, HunkState.FULLY_ADDRESSED)
            return self._post_full_resolution(comment_id, pr_num, fingerprint)
        else:
            self._transition(HunkState.NOVA_CALLED, HunkState.NOT_FULLY_ADDRESSED)
            return self._post_follow_up(comment_id, pr_num, fingerprint, guidance=result.guidance)

    def _post_full_resolution(
        self, comment_id: str, pr_num: int, fingerprint: str
    ) -> HunkState:
        """Post resolve reply with auto_resolved sentinel."""
        Log.pipeline.debug("[HunkTracker] _post_full_resolution: comment_id=%s fp=%s", comment_id, fingerprint)
        if not comment_id:
            self._transition(HunkState.FULLY_ADDRESSED, HunkState.REPLY_FAILED)
            return HunkState.REPLY_FAILED

        sentinel = self._make_sentinel("auto_resolved", fingerprint)
        reply_body = _RESOLVE_REPLY_TEMPLATE.format(
            message="The code change resolves this issue.", sentinel=sentinel
        )

        ok = self._adapter.resolve_inline_comment(
            pr_id=pr_num, comment_id=comment_id, reply_body=reply_body
        )
        Log.pipeline.debug("[HunkTracker] _post_full_resolution: resolve_inline_comment ok=%s", ok)
        if ok:
            self._transition(HunkState.FULLY_ADDRESSED, HunkState.RESOLVE_REPLY_POSTED)
            return HunkState.RESOLVE_REPLY_POSTED
        self._transition(HunkState.FULLY_ADDRESSED, HunkState.REPLY_FAILED)
        return HunkState.REPLY_FAILED

    def _post_follow_up(
        self, comment_id: str, pr_num: int, fingerprint: str, guidance: str
    ) -> HunkState:
        """Post a follow-up reply with follow_up_posted sentinel (no resolve)."""
        Log.pipeline.debug("[HunkTracker] _post_follow_up: comment_id=%s fp=%s", comment_id, fingerprint)
        if not comment_id:
            self._transition(HunkState.NOT_FULLY_ADDRESSED, HunkState.REPLY_FAILED)
            return HunkState.REPLY_FAILED

        sentinel = self._make_sentinel("follow_up_posted", fingerprint)
        reply_body = _FOLLOW_UP_REPLY_TEMPLATE.format(guidance=guidance, sentinel=sentinel)

        reply_fn = getattr(self._adapter, "reply_to_comment", None)
        if callable(reply_fn):
            reply_id = reply_fn(pr_num, comment_id, reply_body)
            Log.pipeline.debug("[HunkTracker] _post_follow_up: reply posted reply_id=%s", reply_id)
            if reply_id:
                self._transition(HunkState.NOT_FULLY_ADDRESSED, HunkState.FOLLOW_UP_POSTED)
                return HunkState.FOLLOW_UP_POSTED
        else:
            Log.pipeline.warning("adapter lacks reply_to_comment — follow-up not posted for comment %s", comment_id)

        self._transition(HunkState.NOT_FULLY_ADDRESSED, HunkState.REPLY_FAILED)
        return HunkState.REPLY_FAILED


# ---------------------------------------------------------------------------
# API fingerprint helpers
# ---------------------------------------------------------------------------

# Matches the Markdown fingerprint sentinel embedded by BodyBuilder:
#   [//]: # (revue:fp:<16-char hex>)
_FP_SENTINEL_RE = re.compile(r"\[//\]: # \(revue:fp:([a-f0-9]+)\)")
_REVUE_SUMMARY_MARKER = "## 🤖 Revue"


def _apply_location_entry(
    comment: dict,
    body: str,
    effective_id: str,
    result: dict[str, dict],
    gen_fingerprint,
    resolved: bool,
) -> None:
    """Extract fingerprint from comment body or location and add to result."""
    inline = comment.get("inline", {})
    file_path = inline.get("path", "") or ""
    line = inline.get("to") or 0

    # Strategy 1: explicit fingerprint sentinel in body — enrich with inline
    # location so resolution_status() can evaluate CODE_REMOVED correctly.
    m = _FP_SENTINEL_RE.search(body)
    if m:
        fp = m.group(1)
        if fp not in result:
            entry: dict = {"platform_comment_id": effective_id, "resolved": resolved}
            if file_path:
                entry["file_path"] = file_path
            if line:
                entry["line_number"] = int(line)
            result[fp] = entry
        return

    # Strategy 2: location-based fingerprint from inline comment metadata
    if not file_path and _REVUE_SUMMARY_MARKER in body:
        return  # skip summary comments

    if file_path and line:
        fp = gen_fingerprint(file_path, int(line), "")
        if fp not in result:
            result[fp] = {
                "platform_comment_id": effective_id,
                "file_path": file_path,
                "line_number": int(line),
                "resolved": resolved,
            }


# ---------------------------------------------------------------------------
# NullHunkTracker — no-op implementation for when Nova is unavailable
# ---------------------------------------------------------------------------


class _NullResolutionStrategy:
    """Placeholder that must never be called — NullHunkTracker overrides resolution_status."""

    def resolve(self, *args, **kwargs):
        raise RuntimeError("_NullResolutionStrategy.resolve() called — this is a bug")


class NullHunkTracker(HunkTracker):
    """HunkTracker without Nova analysis.

    Inherits build_prior() (dedup store + API scan) but replaces resolution_status()
    with a simple inline-resolve so Poster always receives a real object rather than None.
    """

    def __init__(self, adapter, dedup_store) -> None:
        super().__init__(adapter, dedup_store, _NullResolutionStrategy())

    def resolution_status(
        self,
        fingerprint: str,
        prior_entry: dict,
        new_diff: str,
    ) -> HunkState:
        old_comment_id = prior_entry.get("platform_comment_id")
        pr_num = prior_entry.get("pr_num")
        if not old_comment_id or not pr_num:
            return HunkState.UNTOUCHED
        ok = self._adapter.resolve_inline_comment(
            pr_id=pr_num,
            comment_id=old_comment_id,
            reply_body="✅ Issue appears to be resolved in latest commit.",
        )
        return HunkState.RESOLVE_REPLY_POSTED if ok else HunkState.UNTOUCHED


# ---------------------------------------------------------------------------
# NovaSingleShotResolutionStrategy
# ---------------------------------------------------------------------------


class NovaSingleShotResolutionStrategy:
    """ResolutionStrategy backed by Nova (the existing AI client).

    Calls Nova with the original finding + new diff hunk to determine
    whether the issue is "fully", "partial", or "not" resolved.
    On any failure, raises — callers must handle NOVA_ERROR.
    """

    _RESOLUTION_PROMPT = (
        "You are a senior code reviewer evaluating whether a code change resolves a previous finding.\n\n"
        "Original finding: {issue}\n"
        "Original code context: {code}\n"
        "New code (diff hunk): {new_hunk}\n"
        "{prior_guidance}"
        "\n\nIs the original finding now resolved?\n"
        "Reply in exactly two lines:\n"
        "Line 1 — one word only: fully, partial, or not\n"
        "Line 2 — one sentence explaining what was addressed or what still needs fixing\n"
        "Example:\n"
        "fully\n"
        "The null pointer check now correctly guards against empty input."
    )

    _VERDICT_MAP: dict[str, ResolutionVerdict] = {
        "fully": ResolutionVerdict.FULLY,
        "partial": ResolutionVerdict.PARTIAL,
        "not": ResolutionVerdict.UNRESOLVED,
    }

    def __init__(self, nova_client) -> None:
        if nova_client is None:
            raise ValueError(
                "nova_client is required; use NullHunkTracker when Nova is unavailable"
            )
        self._nova = nova_client

    @staticmethod
    def _sanitize(text: str | None) -> str:
        return (text or "").replace("\r", "").replace("\n", " ")

    def resolve(
        self,
        original_finding: dict,
        new_hunk: str,
        prior_follow_up: str | None = None,
    ) -> ResolutionResult:
        """Return ResolutionResult with verdict and guidance. Raises on any Nova failure."""
        issue = self._sanitize(original_finding.get("issue", ""))
        code = self._sanitize(original_finding.get("code", ""))
        prior_section = (
            f"Prior follow-up guidance given: {self._sanitize(prior_follow_up)}\n"
            if prior_follow_up
            else ""
        )
        prompt = self._RESOLUTION_PROMPT.format(
            issue=issue,
            code=code,
            new_hunk=new_hunk,
            prior_guidance=prior_section,
        )
        response = self._nova.complete(prompt)
        parts = [line.strip() for line in (response or "").strip().splitlines() if line.strip()]
        verdict_str = parts[0].lower() if parts else ""
        guidance = parts[1] if len(parts) > 1 else "Please address the remaining issue."
        verdict = self._VERDICT_MAP.get(verdict_str)
        if verdict is None:
            raise RuntimeError(f"NovaSingleShotResolutionStrategy: unexpected verdict {verdict_str!r}")
        return ResolutionResult(verdict=verdict, guidance=guidance)

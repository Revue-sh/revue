"""Comment resolution service - orchestrates auto-resolution logic."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import CommentState, Platform, PRComment, SummaryComment
from .platform_adapter import get_platform_adapter, BitbucketAdapter
from .file_store import CommentFileStore
from .fingerprint import fingerprint
from .json_store import PerPRCommentStore

_log = logging.getLogger(__name__)


class CommentResolutionService:
    """Business logic for comment auto-resolution."""

    def __init__(self, repo_path: str):
        self.repo = CommentFileStore(repo_path)

    def process_pr_scan(
        self,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int
    ) -> SummaryComment:
        """
        Process comment resolution after PR update.

        This is the main entry point called after Revue scans a PR.

        Steps:
        1. Get all Revue comments for this PR
        2. Check each comment's resolution status on platform
        3. Auto-resolve if code changed
        4. Parse replies for dismissals
        5. Update summary comment
        """
        adapter = get_platform_adapter(platform)
        comments = self.repo.get_comments_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        for comment in comments:
            if comment.state == CommentState.UNRESOLVED:
                self._process_unresolved_comment(comment, adapter, pr_number)

        # Update summary
        return self._update_summary(
            platform, repo_owner, repo_name, pr_number, adapter
        )

    def process_new_review(
        self,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        new_findings: list[dict],
        commit_sha: str = "",
        commit_author: str = ""
    ) -> SummaryComment:
        """
        Compare old fingerprints to new review findings and auto-resolve fixed ones.

        For each existing unresolved comment with a finding_fingerprint:
        - If fingerprint NOT in new findings → auto-resolve
        - If fingerprint still present → leave unresolved
        """
        adapter = get_platform_adapter(platform)
        comments = self.repo.get_comments_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        # Build set of fingerprints from new findings
        new_fps: set[str] = set()
        for f in new_findings:
            line = f.get("line_number") or f.get("line_start", 0)
            fp = fingerprint(f["file_path"], line, f.get("issue", ""))
            new_fps.add(fp)

        for comment in comments:
            if comment.state != CommentState.UNRESOLVED:
                continue
            if not comment.finding_fingerprint:
                continue

            if comment.finding_fingerprint not in new_fps:
                # Finding no longer present — auto-resolve
                adapter.resolve_comment(
                    comment.repo_owner,
                    comment.repo_name,
                    comment.pr_number,
                    comment.platform_comment_id,
                    comment.platform_thread_id,
                )
                msg = f"✅ Fixed in commit {commit_sha} by {commit_author}"
                adapter.post_reply(
                    comment.repo_owner,
                    comment.repo_name,
                    comment.pr_number,
                    comment.platform_comment_id,
                    comment.platform_thread_id,
                    msg,
                )
                self.repo.transition_state(
                    comment.id,
                    CommentState.AUTO_RESOLVED,
                    reason=msg,
                )

        return self._update_summary(
            platform, repo_owner, repo_name, pr_number, adapter
        )

    def _process_unresolved_comment(
        self,
        comment: PRComment,
        adapter,
        pr_number: int,
    ) -> None:
        """Process a single unresolved comment."""
        # Check if manually resolved on platform
        if adapter.is_comment_resolved(
            comment.repo_owner,
            comment.repo_name,
            comment.platform_comment_id
        ):
            # Check if there are replies
            replies = adapter.get_comment_replies(
                comment.repo_owner,
                comment.repo_name,
                pr_number,
                comment.platform_comment_id,
            )

            if replies:
                # Has reply - store it
                reply_text = replies[0].get('body', '')
                self.repo.transition_state(
                    comment.id,
                    CommentState.MANUALLY_RESOLVED_WITH_REPLY,
                    reason="Developer resolved with explanation",
                    developer_reply=reply_text
                )
            else:
                # No reply
                self.repo.transition_state(
                    comment.id,
                    CommentState.MANUALLY_RESOLVED_NO_REPLY,
                    reason="Developer resolved without explanation"
                )
            return

        # Check for developer replies (dismissals)
        replies = adapter.get_comment_replies(
            comment.repo_owner,
            comment.repo_name,
            pr_number,
            comment.platform_comment_id,
        )

        for reply in replies:
            reply_body = reply.get('body', '').lower()

            # High-confidence dismissal keywords
            if self._is_dismissal(reply_body):
                # Auto-resolve and post acknowledgment
                self._auto_resolve_dismissed(comment, adapter, reply['body'])
                return

        # Check if code changed (placeholder - needs diff analysis)
        # For now, just skip auto-resolve based on code changes
        # This will be implemented in next iteration

    def _is_dismissal(self, reply_text: str) -> bool:
        """
        Check if reply indicates dismissal.

        High-confidence keywords:
        - "won't fix", "wontfix", "not fixing"
        - "keeping as-is", "keeping as is"
        - "intentional"
        """
        patterns = [
            r"\bwon'?t\s+fix\b",
            r"\bwontfix\b",
            r"\bnot\s+fixing\b",
            r"\bkeeping\s+as[-\s]?is\b",
            r"\bintentional\b",
            r"\bnot\s+relevant\b"
        ]

        for pattern in patterns:
            if re.search(pattern, reply_text, re.IGNORECASE):
                return True

        return False

    def _auto_resolve_dismissed(
        self,
        comment: PRComment,
        adapter,
        developer_reply: str
    ) -> None:
        """Auto-resolve a dismissed comment."""
        # Try platform API resolution first
        resolved = adapter.resolve_comment(
            comment.repo_owner,
            comment.repo_name,
            comment.pr_number,
            comment.platform_comment_id,
            comment.platform_thread_id,
        )

        if not resolved:
            # Fallback: Post acknowledgment comment
            acknowledgment = f"✅ Revue acknowledged: Developer won't fix this. Marking as resolved.\n\n> {developer_reply}"
            adapter.post_reply(
                comment.repo_owner,
                comment.repo_name,
                comment.pr_number,
                comment.platform_comment_id,
                comment.platform_thread_id,
                acknowledgment,
            )

        # Update state
        self.repo.transition_state(
            comment.id,
            CommentState.DISMISSED_WITH_REASON,
            reason="Developer dismissed with explanation",
            developer_reply=developer_reply
        )

    def _update_summary(
        self,
        platform: Platform,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        adapter
    ) -> SummaryComment:
        """Update or create summary comment."""
        comments = self.repo.get_comments_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        # Calculate counts
        total = len(comments)
        fixed = sum(
            1 for c in comments
            if c.state in [
                CommentState.AUTO_RESOLVED,
                CommentState.MANUALLY_RESOLVED_WITH_REPLY,
                CommentState.MANUALLY_RESOLVED_NO_REPLY
            ]
        )
        discussed = sum(
            1 for c in comments
            if c.state == CommentState.DISMISSED_WITH_REASON
        )
        remaining = sum(
            1 for c in comments
            if c.state == CommentState.UNRESOLVED
        )

        # Get or create summary
        existing_summary = self.repo.get_summary_for_pr(
            platform, repo_owner, repo_name, pr_number
        )

        if existing_summary:
            # Update existing
            existing_summary.total_issues = total
            existing_summary.fixed_count = fixed
            existing_summary.discussed_count = discussed
            existing_summary.remaining_count = remaining
            summary = self.repo.create_or_update_summary(existing_summary)

            # Update comment on platform
            adapter.post_reply(
                repo_owner,
                repo_name,
                pr_number,
                summary.platform_comment_id,
                None,
                summary.format_summary(),
            )
        else:
            # Create new summary comment
            summary_text = f"""🤖 Revue Code Review Summary

📊 **Status:** {total} issues found

This comment will update automatically as you address issues.

---
💬 **How to respond to Revue:**
• Reply "Won't fix" and explain why if you're keeping it as-is
• Reply with a question if you need clarification
• Revue will auto-resolve when you fix the code
"""

            comment_id, thread_id = adapter.post_comment(
                repo_owner,
                repo_name,
                pr_number,
                "README.md",  # Post to README (first file alphabetically)
                1,
                summary_text,
                "HEAD"  # Placeholder - need actual commit SHA
            )

            summary = SummaryComment(
                id=None,
                platform=platform,
                platform_comment_id=comment_id,
                pr_number=pr_number,
                repo_owner=repo_owner,
                repo_name=repo_name,
                total_issues=total,
                fixed_count=fixed,
                discussed_count=discussed,
                remaining_count=remaining,
                last_updated_at=None,
                created_at=None
            )
            summary = self.repo.create_or_update_summary(summary)

        return summary


# ---------------------------------------------------------------------------
# Won't-fix reply tracking service (REVUE-112)
# ---------------------------------------------------------------------------

_LESSONS_BRANCH_PREFIX = "chore/revue-lessons-"
_DEFAULT_LESSONS_PR_BODY = """\
## Summary
Automated lessons learned from Revue won't-fix replies.

## Pattern updates
See changes to `.revue.yml` for added allowed/disallowed patterns.

🤖 Generated by Revue won't-fix reply tracking.
"""


class WontFixReplyService:
    """Orchestrate won't-fix reply tracking for Bitbucket PRs (REVUE-112).

    Dependencies are injected via the constructor (DIP).  No external calls
    are made inside service methods — all I/O goes through the injected
    adapter, consolidator, and store.
    """

    def __init__(
        self,
        repo_path: str,
        ai_client: Any,
        bitbucket_username: str,
        bitbucket_app_password: str,
        repo_owner: str,
        repo_name: str,
        platform: str = "bitbucket",
        adapter: Any = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self._store = PerPRCommentStore(repo_path)
        self._client = ai_client
        self._platform = platform
        if adapter is not None:
            self._adapter = adapter
        else:
            self._adapter = BitbucketAdapter(bitbucket_username, bitbucket_app_password)
        self._bb_username = bitbucket_username
        self._bb_password = bitbucket_app_password
        self._repo_owner = repo_owner
        self._repo_name = repo_name

    # ------------------------------------------------------------------
    # Phase 2 API: classify / respond split (REVUE-112 Phase 2, AC14)
    # ------------------------------------------------------------------

    def classify(self, pr_number: int) -> "ClassificationResult":
        """Query phase: collect thread replies and classify developer intent.

        Side-effect free: no file writes, no API POSTs, no store mutations
        (AC21, REVUE-112 Phase 2).  Returns a ClassificationResult the pipeline
        uses to patch config before agents run.  Pass the result to respond()
        for all I/O.

        Raises:
            Exception: Any exception from the AI call is re-raised (AC10).
        """
        from revue.core.models import ClassificationResult
        from revue.core.dedup_consolidator import NovaConsolidator

        consolidator = NovaConsolidator(self._client)

        threads = self._collect_threads_with_replies(pr_number)
        if not threads:
            _log.info(
                "[REVUE-112] No threads with replies for PR #%d — skipping classify.",
                pr_number,
            )
            return ClassificationResult(
                patterns_to_allow=[],
                patterns_to_disallow=[],
                state_updates=[],
                decisions=[],
            )

        # Threads where the bot's sentinel is the last reply have no new developer
        # input — skip the AI call for them to avoid confusion and re-posting.
        threads_for_ai = [t for t in threads if not t.get("already_handled")]
        already_handled_decisions: list[dict] = [
            {"fingerprint": t["fingerprint"], "decision": "already_handled", "reply_draft": ""}
            for t in threads
            if t.get("already_handled")
        ]

        if not threads_for_ai:
            return ClassificationResult(
                patterns_to_allow=[],
                patterns_to_disallow=[],
                state_updates=[],
                decisions=already_handled_decisions,
            )

        try:
            decisions = consolidator.analyse_reply_threads(threads_for_ai)
            decisions += already_handled_decisions
        except Exception:
            _log.exception(
                "[REVUE-112] analyse_reply_threads failed for PR #%d. Thread count=%d",
                pr_number,
                len(threads_for_ai),
            )
            return ClassificationResult(
                patterns_to_allow=[],
                patterns_to_disallow=[],
                state_updates=[],
                decisions=[],
            )

        patterns_to_allow: list[dict] = []
        patterns_to_disallow: list[dict] = []
        state_updates: list[dict] = []

        for decision in decisions:
            fp = decision.get("fingerprint", "")
            dec = decision.get("decision", "")
            thread_entry = next((t for t in threads if t["fingerprint"] == fp), None)
            file_path = thread_entry.get("file_path", "") if thread_entry else ""

            if dec == "allowed_pattern":
                patterns_to_allow.append({
                    "pattern": decision.get("pattern", ""),
                    "rationale": decision.get("rationale", ""),
                })
                state_updates.append({"fingerprint": fp, "file_path": file_path, "decision": dec})
            elif dec == "disallowed_pattern":
                patterns_to_disallow.append({
                    "pattern": decision.get("pattern", ""),
                    "rationale": decision.get("rationale", ""),
                })
                state_updates.append({"fingerprint": fp, "file_path": file_path, "decision": dec})

        return ClassificationResult(
            patterns_to_allow=patterns_to_allow,
            patterns_to_disallow=patterns_to_disallow,
            state_updates=state_updates,
            decisions=decisions,
        )

    def apply_state_updates(self, result: "ClassificationResult", pr_number: int) -> None:
        """Apply store state updates from a ClassificationResult (REVUE-112 Phase 2, AC18).

        Marks resolved fingerprints as WONT_FIX in the per-PR store.  Called by
        the pipeline between classify() and diff parsing so the dedup layer sees
        up-to-date state when agents run.  Keeps CommentState inside the comments
        layer — pipeline.py must not import it directly.
        """
        for update in result.state_updates:
            self._store.mark_resolved(
                "bitbucket",
                pr_number,
                update.get("file_path", ""),
                update.get("fingerprint", ""),
                CommentState.WONT_FIX,
                reason=update.get("decision", ""),
            )

    # ------------------------------------------------------------------
    # Main entry point (thin wrapper — backward compat, AC14)
    # ------------------------------------------------------------------

    def process_wont_fix_replies(self, pr_number: int) -> None:
        """Thin wrapper: classify then respond in sequence (AC7, AC14).

        Split into classify() (zero side-effects) and respond() (all I/O) so
        the pipeline can run classify before agents and respond after comment
        posting (REVUE-112 Phase 2).
        """
        result = self.classify(pr_number)
        self.respond(result, pr_number)

    # Appended to every bot reply so respond() can detect it already ran on a thread.
    _BOT_ACK_SENTINEL = "[//]: # (revue:ack)"
    # Appended in addition to _BOT_ACK_SENTINEL for terminal decisions (wont-fix,
    # acknowledged_fixed, acknowledged_deferred). Allows respond() to call
    # resolve_comment on a re-run if the thread was not resolved on the first pass.
    _BOT_RESOLVED_SENTINEL = "[//]: # (revue:resolved)"

    def respond(self, result: "ClassificationResult", pr_number: int) -> None:
        """I/O phase: act on the classified decisions (REVUE-112 Phase 2, AC14).

        All side-effects live here: .revue.yml writes, lessons PR creation /
        update, thread replies, store state updates.  Receives the
        ClassificationResult produced by classify() — never re-runs the AI call.

        Steps are sequential (AC7): per decision →
          allowed_pattern / disallowed_pattern → write config, create/update
            lessons PR (wait for PR#), post reply including PR link.
          reason_missing → post reply asking for reason (no state change).
          not_acknowledged → post reply reaffirming finding (no state change).
          already_handled → skip silently (classify() detected a prior bot reply).

        Idempotent: skips any thread where the bot already posted a reply
        containing _BOT_ACK_SENTINEL from a previous run.
        """
        if not result.decisions:
            return

        # Re-fetch threads to look up platform_comment_id for each fingerprint.
        threads = self._collect_threads_with_replies(pr_number)
        thread_by_fp = {t["fingerprint"]: t for t in threads}
        print(f"[revue]   💬  respond(): {len(result.decisions)} decision(s), {len(threads)} thread(s) re-fetched.", flush=True)

        lessons_pr_url: Optional[str] = None

        for decision in result.decisions:
            fingerprint_val = decision.get("fingerprint", "")
            dec = decision.get("decision", "")
            reply_draft = decision.get("reply_draft", "")

            if dec == "already_handled":
                # If the bot's last reply carried revue:resolved, the thread was a
                # terminal decision that may not have been resolved yet (e.g. posted
                # manually, or resolve_comment failed on the previous run). Retry.
                terminal_thread = thread_by_fp.get(fingerprint_val)
                if terminal_thread and terminal_thread.get("already_terminal"):
                    t_comment_id = terminal_thread.get("platform_comment_id", "")
                    t_thread_id = terminal_thread.get("thread_id")
                    try:
                        ok = self._adapter.resolve_comment(
                            self._repo_owner, self._repo_name, pr_number,
                            t_comment_id, t_thread_id,
                        )
                        if ok:
                            print(
                                f"[revue]   💬  respond(): resolved terminal thread {t_comment_id} (recovery)",
                                flush=True,
                            )
                        else:
                            _log.warning(
                                "[REVUE-112] resolve_comment returned False for terminal recovery, comment %s",
                                t_comment_id,
                            )
                    except Exception:
                        _log.exception(
                            "[REVUE-112] resolve_comment failed for terminal recovery, comment %s",
                            t_comment_id,
                        )
                else:
                    print(f"[revue]   💬  respond(): skip {fingerprint_val} — already handled", flush=True)
                continue

            thread_entry = thread_by_fp.get(fingerprint_val)
            if thread_entry is None:
                _log.warning(
                    "[REVUE-112] No thread found for fingerprint %s — skipping respond.",
                    fingerprint_val,
                )
                continue

            # Idempotency guard: skip if bot's LAST reply contains the sentinel.
            # Using last-reply only means a new developer reply after the sentinel
            # will re-enable re-evaluation on the next classify/respond cycle.
            existing_replies = thread_entry.get("replies", [])
            last_reply = existing_replies[-1] if existing_replies else ""
            if self._BOT_ACK_SENTINEL in last_reply or self._BOT_RESOLVED_SENTINEL in last_reply:
                print(
                    f"[revue]   💬  respond(): skip {thread_entry.get('platform_comment_id')} ({dec}) — already acknowledged",
                    flush=True,
                )
                continue

            comment_id = thread_entry.get("platform_comment_id", "")
            thread_id = thread_entry.get("thread_id")
            file_path = thread_entry.get("file_path", "")
            print(f"[revue]   💬  respond(): fp={fingerprint_val} dec={dec} comment_id={comment_id}", flush=True)

            if dec in ("allowed_pattern", "disallowed_pattern"):
                pattern = decision.get("pattern", "")
                rationale = decision.get("rationale", "")

                # (a) Update .revue.yml
                self._append_pattern_to_config(dec, pattern, rationale, pr_number)

                # (b) Create/update lessons PR — if this fails, warn and fall back to YAML block
                try:
                    if lessons_pr_url is None:
                        lessons_pr_url = self._ensure_lessons_pr(
                            pr_number, pattern, rationale, dec
                        )
                    else:
                        # Adapter handles idempotency (finds existing PR, appends pattern).
                        # Call again rather than the removed _commit_pattern_to_lessons_branch.
                        self._ensure_lessons_pr(pr_number, pattern, rationale, dec)
                    final_reply = reply_draft.replace("[LESSONS_PR_URL]", lessons_pr_url)
                except Exception:
                    _log.warning(
                        "[REVUE-112] Lessons PR creation/update failed for PR #%d pattern '%s'. "
                        "Posting YAML block for manual apply.",
                        pr_number,
                        pattern[:60],
                    )
                    yaml_block = (
                        f"Could not open a lessons PR automatically. "
                        f"Please add the following to `.revue.yml` manually:\n\n"
                        f"```yaml\nnoise_filters:\n"
                        f"  {'allowed_patterns' if dec == 'allowed_pattern' else 'disallowed_patterns'}:\n"
                        f"    - pattern: \"{pattern}\"\n"
                        f"      rationale: \"{rationale}\"\n```"
                    )
                    final_reply = yaml_block

                # Update state to wont_fix
                self._store.mark_resolved(
                    self._platform,
                    pr_number,
                    file_path,
                    fingerprint_val,
                    CommentState.WONT_FIX,
                    reason=f"{dec}: {pattern}",
                )

                # (c) Post reply (both sentinels: ack for idempotency, resolved for recovery)
                try:
                    self._adapter.post_reply(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                        final_reply + f"\n\n{self._BOT_ACK_SENTINEL}\n{self._BOT_RESOLVED_SENTINEL}",
                    )
                    print(f"[revue]   💬  respond(): replied to comment {comment_id} ({dec})", flush=True)
                except Exception:
                    _log.exception(
                        "[REVUE-112] post_reply failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )

                # (d) Resolve the thread — won't-fix is a closed decision
                try:
                    ok = self._adapter.resolve_comment(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                    )
                    if ok:
                        print(f"[revue]   💬  respond(): resolved thread {comment_id} ({dec})", flush=True)
                    else:
                        _log.warning(
                            "[REVUE-112] resolve_comment returned False for comment %s (decision=%s) on PR #%d",
                            comment_id, dec, pr_number,
                        )
                except Exception:
                    _log.exception(
                        "[REVUE-112] resolve_comment failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )

            elif dec == "reason_missing":
                # AC8: post reply asking for reason, do NOT update state
                if not reply_draft:
                    reply_draft = (
                        "Thanks for the reply. Revue needs a clear reason to record "
                        "this decision — could you briefly explain why this finding is "
                        "acceptable or out of scope? Once a reason is provided, Revue "
                        "will record it and won't flag this pattern again."
                    )
                try:
                    self._adapter.post_reply(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                        reply_draft + f"\n\n{self._BOT_ACK_SENTINEL}",
                    )
                    print(f"[revue]   💬  respond(): replied to comment {comment_id} ({dec})", flush=True)
                except Exception:
                    _log.exception(
                        "[REVUE-112] post_reply failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )

            elif dec == "not_acknowledged":
                # AC9: post reply reaffirming finding, do NOT update state
                try:
                    self._adapter.post_reply(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                        reply_draft + f"\n\n{self._BOT_ACK_SENTINEL}",
                    )
                    print(f"[revue]   💬  respond(): replied to comment {comment_id} ({dec})", flush=True)
                except Exception:
                    _log.exception(
                        "[REVUE-112] post_reply failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )

            elif dec == "acknowledged_deferred":
                # Developer acknowledged the finding and provided a deferral reason.
                # No lessons PR or .revue.yml write — this is not a permanent decision.
                # Post confirmation reply (with sentinel) and close the thread.
                if reply_draft:
                    try:
                        self._adapter.post_reply(
                            self._repo_owner,
                            self._repo_name,
                            pr_number,
                            comment_id,
                            thread_id,
                            reply_draft + f"\n\n{self._BOT_ACK_SENTINEL}\n{self._BOT_RESOLVED_SENTINEL}",
                        )
                        print(f"[revue]   💬  respond(): replied to comment {comment_id} ({dec})", flush=True)
                    except Exception:
                        _log.exception(
                            "[REVUE-112] post_reply failed for comment %s (decision=%s) on PR #%d",
                            comment_id, dec, pr_number,
                        )
                try:
                    ok = self._adapter.resolve_comment(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                    )
                    if ok:
                        print(f"[revue]   💬  respond(): resolved thread {comment_id} ({dec})", flush=True)
                    else:
                        _log.warning(
                            "[REVUE-112] resolve_comment returned False for comment %s (decision=%s) on PR #%d",
                            comment_id, dec, pr_number,
                        )
                except Exception:
                    _log.exception(
                        "[REVUE-112] resolve_comment failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )

            elif dec == "acknowledged_fixed":
                # Developer fixed the code — post acknowledgment and resolve the thread
                self._store.mark_resolved(
                    self._platform,
                    pr_number,
                    file_path,
                    fingerprint_val,
                    CommentState.RESOLVED,
                    reason="acknowledged_fixed",
                )
                try:
                    self._adapter.post_reply(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                        reply_draft + f"\n\n{self._BOT_ACK_SENTINEL}\n{self._BOT_RESOLVED_SENTINEL}",
                    )
                    print(f"[revue]   💬  respond(): replied to comment {comment_id} ({dec})", flush=True)
                except Exception:
                    _log.exception(
                        "[REVUE-112] post_reply failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )
                try:
                    ok = self._adapter.resolve_comment(
                        self._repo_owner,
                        self._repo_name,
                        pr_number,
                        comment_id,
                        thread_id,
                    )
                    if ok:
                        print(f"[revue]   💬  respond(): resolved thread {comment_id} ({dec})", flush=True)
                    else:
                        _log.warning(
                            "[REVUE-112] resolve_comment returned False for comment %s (decision=%s) on PR #%d",
                            comment_id, dec, pr_number,
                        )
                except Exception:
                    _log.exception(
                        "[REVUE-112] resolve_comment failed for comment %s (decision=%s) on PR #%d",
                        comment_id, dec, pr_number,
                    )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Matches the opening line of every Revue finding comment.
    _FINDING_PATTERN = re.compile(
        r'^\*\*(?:🔴|🟡|🔵|ℹ️)\s*\[(?:HIGH|MEDIUM|LOW|INFO)\]',
    )

    def _collect_threads_with_replies(self, pr_number: int) -> list[dict]:
        """Discover Revue finding comments and their developer replies via API.

        Queries the VCS API once for all PR comments, then does all filtering
        in memory.  No local store reads — works on fresh CI checkouts.
        Eliminates the previous N+1 (one get_comment_replies call per finding).

        Finding comments are identified by the Revue severity-badge format:
          **🔴 [HIGH] ..., **🟡 [MEDIUM] ..., **🔵 [LOW] ..., **ℹ️ [INFO] ...

        Fingerprint = str(comment_id) — stable, unique, no content hashing needed.
        """
        print(f"[revue]   💬  Fetching all comments for PR #{pr_number}...", flush=True)
        try:
            all_comments = self._adapter.get_all_pr_comments(
                self._repo_owner, self._repo_name, pr_number
            )
        except Exception:
            _log.exception(
                "[REVUE-112] Failed to fetch PR comments for PR #%d", pr_number
            )
            print(f"[revue]   💬  ERROR: could not fetch PR comments — won't-fix tracking skipped.", flush=True)
            return []

        print(f"[revue]   💬  {len(all_comments)} total comment(s) fetched.", flush=True)

        # Top-level inline comments matching the Revue finding format
        top_level_inline = [
            c for c in all_comments if not c.get("parent") and c.get("inline")
        ]
        print(
            f"[revue]   💬  {len(top_level_inline)} top-level inline comment(s) (non-inline/summary excluded).",
            flush=True,
        )

        finding_comments: dict[str, dict] = {}
        skipped_resolved = 0
        for c in top_level_inline:
            body = c.get("content", {}).get("raw", "")
            if self._FINDING_PATTERN.match(body):
                if c.get("resolution") is not None:
                    skipped_resolved += 1
                else:
                    finding_comments[str(c["id"])] = c

        resolved_note = f", {skipped_resolved} already resolved" if skipped_resolved else ""
        print(
            f"[revue]   💬  {len(finding_comments)} Revue finding(s) matched format filter{resolved_note}.",
            flush=True,
        )

        if not finding_comments:
            # Diagnostic: show first few top-level inline bodies to help spot regex mismatches
            samples = [
                c.get("content", {}).get("raw", "")[:80].replace("\n", " ")
                for c in top_level_inline[:3]
            ]
            if samples:
                print(f"[revue]   💬  Sample non-matching bodies: {samples}", flush=True)
            return []

        # Build reverse map: platform_comment_id → store fingerprint.
        # When the local store has entries (local dev, or persisted CI), use the
        # store's content-hash fingerprint so apply_state_updates() can update
        # the correct store entry.  On fresh CI (empty store), fall back to
        # comment_id — state updates are best-effort no-ops in that case.
        unresolved = self._store.get_unresolved_fingerprints(self._platform, pr_number)
        comment_id_to_fp: dict[str, str] = {
            entry.get("platform_comment_id", ""): fp
            for fp, entry in unresolved.items()
        }

        # Group replies by parent comment ID — in memory, zero extra API calls
        replies_by_parent: dict[str, list[dict]] = {}
        for c in all_comments:
            pid = str((c.get("parent") or {}).get("id", ""))
            if pid in finding_comments:
                replies_by_parent.setdefault(pid, []).append(c)

        findings_with_replies = sum(
            1 for cid in finding_comments if replies_by_parent.get(cid)
        )
        print(
            f"[revue]   💬  {findings_with_replies}/{len(finding_comments)} finding(s) have replies.",
            flush=True,
        )

        threads: list[dict] = []
        for comment_id, finding in finding_comments.items():
            replies = replies_by_parent.get(comment_id, [])
            if not replies:
                continue

            inline = finding.get("inline", {})
            body = finding.get("content", {}).get("raw", "")
            fp = comment_id_to_fp.get(comment_id, comment_id)
            # inline may be a bool (GitHub: True) or a dict (Bitbucket: {"path": ..., "to": ...})
            inline_dict = inline if isinstance(inline, dict) else {}
            reply_texts = [r.get("content", {}).get("raw", "") for r in replies]
            # If the bot's sentinel is in the LAST reply, no new developer input has
            # arrived since we last responded — skip the AI call for this thread.
            last_reply_text = reply_texts[-1] if reply_texts else ""
            sentinel_is_last = bool(reply_texts) and (
                self._BOT_ACK_SENTINEL in last_reply_text
                or self._BOT_RESOLVED_SENTINEL in last_reply_text
            )
            # Terminal flag: the last reply contains revue:resolved, meaning a
            # definitive won't-fix decision was posted. respond() will attempt to
            # resolve the thread on the next run if it wasn't resolved already.
            terminal_is_last = bool(reply_texts) and self._BOT_RESOLVED_SENTINEL in last_reply_text
            threads.append({
                "fingerprint": fp,
                "file_path": inline_dict.get("path", ""),
                "line": inline_dict.get("to", 0) or 0,
                "issue_type": self._issue_type_from_body(body),
                "severity": self._severity_from_body(body),
                "original_finding_summary": body[:300],
                "replies": reply_texts,
                "platform_comment_id": comment_id,
                "thread_id": finding.get("thread_id"),
                "already_handled": sentinel_is_last,
                "already_terminal": terminal_is_last,
            })

        return threads

    @staticmethod
    def _severity_from_body(body: str) -> str:
        if "🔴" in body[:30]:
            return "high"
        if "🟡" in body[:30]:
            return "medium"
        if "🔵" in body[:30]:
            return "low"
        return "info"

    @staticmethod
    def _issue_type_from_body(body: str) -> str:
        lower = body[:150].lower()
        if "security" in lower:
            return "security"
        if "architecture" in lower:
            return "architecture"
        if "performance" in lower:
            return "performance"
        return "code_quality"

    def _revue_yml_path(self) -> Path:
        return self.repo_path / ".revue.yml"

    def _append_pattern_to_config(
        self,
        decision: str,
        pattern: str,
        rationale: str,
        pr_number: int,
    ) -> None:
        """Append a pattern entry to .revue.yml under noise_filters."""
        revue_yml = self._revue_yml_path()
        if revue_yml.exists():
            with open(revue_yml, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

        section = "allowed_patterns" if decision == "allowed_pattern" else "disallowed_patterns"
        noise_filters = config.setdefault("noise_filters", {})
        patterns_list = noise_filters.setdefault(section, [])
        patterns_list.append({"pattern": pattern, "rationale": rationale})

        with open(revue_yml, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    def _lessons_branch_name(self, pr_number: int) -> str:
        return f"{_LESSONS_BRANCH_PREFIX}{pr_number}"

    def _ensure_lessons_pr(
        self,
        pr_number: int,
        pattern: str,
        rationale: str,
        decision: str,
    ) -> str:
        """Delegate lessons PR creation/update to the platform adapter. Returns PR/MR URL.

        Reads the already-updated .revue.yml from disk and passes its content
        to the adapter along with a commit message and PR metadata. The adapter
        owns all platform-specific API logic (AC4, AC5, AC6, REVUE-120).
        """
        branch = self._lessons_branch_name(pr_number)
        revue_yml = self._revue_yml_path()
        content = revue_yml.read_text(encoding="utf-8") if revue_yml.exists() else ""

        section_label = "allowed_pattern" if decision == "allowed_pattern" else "disallowed_pattern"
        short_pattern = pattern[:60] + ("…" if len(pattern) > 60 else "")
        commit_msg = (
            f"chore: add {section_label} for {short_pattern}\n\n"
            f"{rationale}. Recorded via Revue won't-fix reply on PR #{pr_number}."
        )

        template = self._adapter.get_pr_template(self._repo_owner, self._repo_name)
        pr_description = template if template else _DEFAULT_LESSONS_PR_BODY

        return self._adapter.ensure_lessons_pr(
            repo_owner=self._repo_owner,
            repo_name=self._repo_name,
            pr_number=pr_number,
            branch=branch,
            revue_yml_content=content,
            commit_message=commit_msg,
            pr_title=f"chore: Revue lessons learned from PR #{pr_number}",
            pr_description=pr_description,
        )

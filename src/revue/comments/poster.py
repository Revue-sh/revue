"""Poster: I/O layer for review comment posting (REVUE-211).

Responsibilities:
- Position resolution (DiffPositionResolver.snap before posting)
- Per-finding fingerprint dedup (against PerPRCommentStore + live API)
- VCSAdapter call (_post_or_evict_and_retry)
- Summary comment post/update with platform-aware ordering
- HunkTracker delegation for prior-comment resolution (AC3)

Does NOT:
- Import concrete adapter classes
- Render comment bodies (delegates to BodyBuilder)
- Make grouping/synthesis decisions (that's Consolidator's job)
- Know about sentinel format (delegates to HunkTracker)

Architecture ref: docs/architecture/comment-posting.md
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from revue.comments.body_builder import BodyBuilder
from revue.comments.fingerprint import fingerprint as gen_fingerprint
from revue.comments.models import (
    Attribution,
    CommentState,
    ConsolidatedFinding,
    Platform,
    SummaryComment,
)
from revue.core.diff_position_resolver import DiffPositionResolver
from revue.core.vcs_adapter import DiffPosition, compute_gitlab_line_code

from revue.core.logging_channels import Log

# Matches the severity badge in existing Revue comment bodies.
_FINDING_SEV_RE = re.compile(r"\*\*(?:🔴|🟡|🔵|ℹ️)\s*\[(HIGH|MEDIUM|LOW|INFO)\]")
# Matches the fingerprint sentinel embedded in Revue comments.
_FP_SENTINEL_RE = re.compile(r"\[//\]: # \(revue:fp:([a-f0-9]+)\)")
# Matches the Revue summary header
_REVUE_SUMMARY_MARKER = "## 🤖 Revue.io — Code Review"
_REVISION_RE = re.compile(r"Review #(\d+)")

# ---------------------------------------------------------------------------
# Platform registry — drives all platform branching (OCP, AC9).
# Adding a new platform requires only a new registry entry, not code changes.
# ---------------------------------------------------------------------------

# Platforms where position==0 means "outside diff" (unanchored → summary_sink).
_UNANCHORED_PLATFORMS: frozenset[str] = frozenset({"github", "bitbucket"})
# Platforms where summary is posted AFTER inline comments (newest-first display).
_NEWEST_FIRST_PLATFORMS: frozenset[str] = frozenset({"gitlab", "bitbucket"})


def _gitlab_position_resolver(adapter, file_path: str, line_number: int, diff_content: str) -> DiffPosition:
    lc, resolved_line, old_ln = compute_gitlab_line_code(file_path, diff_content, line_number)
    return DiffPosition(
        file_path=file_path, line_number=resolved_line,
        line_code=lc, new_line=resolved_line,
        old_line=old_ln if old_ln > 0 else None, side="RIGHT",
    )


_POSITION_RESOLVERS: dict[str, object] = {
    "gitlab": _gitlab_position_resolver,
}


class Poster:
    """I/O: position resolution + VCSAdapter call + dedup against existing comments.

    All platform-specific adapter details are hidden behind the VCSAdapter
    Protocol — Poster never imports concrete adapter classes.

    Constructor injection (CLAUDE.md §Architecture rules):
        adapter       — VCSAdapter Protocol implementation
        platform_str  — "bitbucket", "github", or "gitlab"
        platform_enum — Platform enum value for CommentFileStore
        dedup_store   — PerPRCommentStore for fingerprint persistence
        summary_store — CommentFileStore for summary comment tracking
        diff_by_file  — {file_path: per-file diff string} from diff_parser
        hunk_tracker  — optional HunkTracker for prior-comment resolution (AC3)
    """

    def __init__(
        self,
        adapter,
        platform_str: str,
        platform_enum: Platform,
        dedup_store,
        summary_store,
        diff_by_file: dict[str, str],
        hunk_tracker,
    ) -> None:
        self._adapter = adapter
        self._platform_str = platform_str
        self._platform_enum = platform_enum
        self._dedup_store = dedup_store
        self._summary_store = summary_store
        self._diff_by_file = diff_by_file
        self._hunk_tracker = hunk_tracker
        self._builder = BodyBuilder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post(
        self,
        pr_id: int,
        review_results: list,
        comment_style: str,
        repo_owner: str,
        repo_name: str,
        pr_label: str = "PR",
        fallback_mode: str = "normal",
        show_reviewed_files: bool = True,
        rating_cfg: dict | None = None,
    ) -> tuple[int, int]:
        """Post review findings to the VCS platform.

        Returns (posted, failed) counts.
        """
        pr_num = int(pr_id)

        # Load summary state (for revision tracking)
        existing_summary = self._summary_store.get_summary_for_pr(
            platform=self._platform_enum,
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_num,
        )
        revision = (existing_summary.revision + 1) if existing_summary else 1
        scanned: Optional[tuple[str, int]] = None

        if existing_summary is None:
            scanned = self._scan_for_existing_summary(pr_num)
            if scanned is not None:
                revision = scanned[1] + 1

        if comment_style == "per-issue":
            return self._post_per_issue(
                pr_num=pr_num,
                review_results=review_results,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_label=pr_label,
                fallback_mode=fallback_mode,
                show_reviewed_files=show_reviewed_files,
                rating_cfg=rating_cfg,
                existing_summary=existing_summary,
                scanned=scanned,
                revision=revision,
            )

        return self._post_summary_mode(
            pr_num=pr_num,
            review_results=review_results,
            pr_label=pr_label,
            fallback_mode=fallback_mode,
            show_reviewed_files=show_reviewed_files,
            rating_cfg=rating_cfg,
            existing_summary=existing_summary,
            scanned=scanned,
            revision=revision,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    # ------------------------------------------------------------------
    # Per-issue mode
    # ------------------------------------------------------------------

    def _post_per_issue(
        self,
        pr_num: int,
        review_results: list,
        repo_owner: str,
        repo_name: str,
        pr_label: str,
        fallback_mode: str,
        show_reviewed_files: bool,
        rating_cfg: dict | None,
        existing_summary,
        scanned,
        revision: int,
    ) -> tuple[int, int]:
        """Core per-issue posting flow with dedup, position resolution, and summary."""
        # Build prior fingerprint set via HunkTracker so sentinel_state
        # (follow_up_posted / auto_resolved) is included in each entry.
        merged_prior: dict[str, dict] = self._hunk_tracker.build_prior(
            self._platform_str, pr_num
        )

        posted = 0
        skipped = 0
        failed = 0
        previously_tracked = 0
        total_findings: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
        summary_sink: list[ConsolidatedFinding] = []
        eviction_state: list[bool] = [False]
        seen_hunk_fps: set[str] = set()

        # GitHub posts summary first (oldest-first display → stays pinned at top)
        is_gitlab_order = self._platform_str in _NEWEST_FIRST_PLATFORMS
        if not is_gitlab_order:
            # Pre-count for a preliminary GitHub summary
            for rr in review_results:
                if rr.error or not rr.response:
                    continue
                try:
                    findings, _ = _parse_findings(rr.response)
                    for f in findings:
                        sev = (f.get("severity") or "info").lower()
                        if sev in total_findings:
                            total_findings[sev] += 1
                except Exception:
                    pass
            summary_body = self._build_summary_body(
                review_results=review_results,
                total_findings=total_findings,
                revision=revision,
                fallback_mode=fallback_mode,
                show_reviewed_files=show_reviewed_files,
                rating_cfg=rating_cfg,
            )
            self._post_or_update_summary(
                pr_num=pr_num,
                body=summary_body,
                existing_summary=existing_summary,
                scanned=scanned,
                revision=revision,
                total_findings=total_findings,
                repo_owner=repo_owner,
                repo_name=repo_name,
            )
            # Re-fetch so Phase 2 can update the preliminary in-place
            existing_summary = self._summary_store.get_summary_for_pr(
                platform=self._platform_enum,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_num,
            )

        # --- Dedup loop ---
        total_findings = {"high": 0, "medium": 0, "low": 0, "info": 0}

        # Phase 1: collect findings grouped by (file, line)
        groups: dict[tuple[str, int], list[tuple[str, str, str, str, str, dict]]] = {}
        for rr in review_results:
            if rr.error or not rr.response:
                continue
            try:
                findings, _ = _parse_findings(rr.response)
            except Exception:
                continue
            diff_content = self._diff_by_file.get(rr.file_path, "")
            for f in findings:
                try:
                    sev, issue, details, rec, cat, line = _extract_finding_fields(f)
                except Exception:
                    continue
                if not issue and not details:
                    continue
                fp = gen_fingerprint(rr.file_path, line, diff_content)
                seen_hunk_fps.add(fp)
                key = (rr.file_path, line)
                if key not in groups:
                    groups[key] = []
                groups[key].append((sev, issue, rec, details, cat, f))

        # Phase 2: dedup + post each group
        for (file_path, line), group_items in groups.items():
            diff_content = self._diff_by_file.get(file_path, "")
            fp = gen_fingerprint(file_path, line, diff_content)

            matched_entry = merged_prior.get(fp) or merged_prior.get(
                gen_fingerprint(file_path, line, "")
            )
            if matched_entry is not None:
                if matched_entry.get("resolved", False):
                    previously_tracked += len(group_items)
                else:
                    prior_sev = matched_entry.get("severity", "")
                    if not prior_sev:
                        cb = matched_entry.get("comment_body", "")
                        m = _FINDING_SEV_RE.search(cb)
                        prior_sev = m.group(1).lower() if m else ""
                    for sev, *_ in group_items:
                        count_sev = prior_sev if prior_sev in total_findings else sev
                        if count_sev in total_findings:
                            total_findings[count_sev] += 1
                skipped += len(group_items)
                continue

            for sev, *_ in group_items:
                if sev in total_findings:
                    total_findings[sev] += 1

            replacement_line_count = 1
            body, replacement_line_count = self._build_comment_body(
                file_path, line, group_items, fp, replacement_line_count
            )

            # Snap line to diff before resolving position
            snapped_line = DiffPositionResolver.snap(line, diff_content)
            if snapped_line != line:
                replacement_line_count = 1
            elif replacement_line_count > 1:
                end_line = snapped_line + replacement_line_count - 1
                if not DiffPositionResolver.line_in_diff(end_line, diff_content):
                    replacement_line_count = 1

            position = self._resolve_position(file_path, snapped_line, diff_content)

            # Unanchored: position=0 on platforms where 0 means outside diff → summary_sink (AC2)
            if self._platform_str in _UNANCHORED_PLATFORMS and position.position == 0:
                from revue.core.agent_loader import filter_code_replacement
                for _sev, _iss, _rec, _det, _cat, _f in group_items:
                    summary_sink.append(ConsolidatedFinding(
                        file_path=file_path, line_number=line,
                        severity=_sev,  # type: ignore[arg-type]
                        issue=_iss or "Issue found", suggestion=_rec or "",
                        confidence=float(_f.get("confidence", 0.8)),
                        category=_cat or "general",
                        attribution=[Attribution(
                            agent_name=_f.get("agent_name", "unknown"),
                            category=_cat or "general",
                        )],
                        code_replacement=filter_code_replacement(_f.get("code_replacement")),
                        replacement_line_count=_f.get("replacement_line_count", 1),
                        snippet="",
                    ))
                skipped += len(group_items)
                continue

            comment_id = self._post_or_evict_and_retry(
                pr_num=pr_num, position=position, body=body,
                eviction_state=eviction_state,
                replacement_line_count=replacement_line_count,
            )

            if comment_id is not None:
                posted += 1
                self._dedup_store.save_finding(
                    platform=self._platform_str,
                    pr_number=pr_num,
                    file_path=file_path,
                    fingerprint=fp,
                    platform_comment_id=comment_id,
                    line_number=line,
                    comment_body=body,
                )
            else:
                failed += 1

        # AC5: auto-resolve findings absent from new review.
        # A finding is a candidate for auto-resolve only if its file was actually
        # touched in this push (appears in diff or in review_results). Findings on
        # files that were not reviewed at all remain open.
        files_in_diff = set(self._diff_by_file.keys())
        files_in_review = {rr.file_path for rr in review_results if not rr.error}
        touched_files = files_in_diff | files_in_review
        resolved_fps = set(merged_prior.keys()) - seen_hunk_fps
        for fp in resolved_fps:
            entry = merged_prior[fp]
            file_path = entry.get("file_path", "")
            if file_path and file_path not in touched_files:
                continue  # file untouched this push — finding still open

            old_comment_id = entry.get("platform_comment_id")
            if not old_comment_id:
                continue

            diff_content = self._diff_by_file.get(file_path, "")
            state = self._hunk_tracker.resolution_status(
                fingerprint=fp,
                prior_entry={**entry, "pr_num": pr_num},
                new_diff=diff_content,
            )
            from revue.comments.models import HunkState
            if state in (HunkState.RESOLVE_REPLY_POSTED, HunkState.AUTO_RESOLVED):
                self._dedup_store.mark_resolved(
                    platform=self._platform_str,
                    pr_number=pr_num,
                    file_path=file_path,
                    fingerprint=fp,
                    state=CommentState.AUTO_RESOLVED,
                    reason="auto-resolved-hunktracker",
                )

        # All platforms: post/update summary with final post-dedup counts.
        # GitLab/Bitbucket: first and only summary (newest-first → lands at top).
        # GitHub: updates the preliminary summary posted above with accurate counts.
        summary_body = self._build_summary_body(
            review_results=review_results,
            total_findings=total_findings,
            revision=revision,
            fallback_mode=fallback_mode,
            show_reviewed_files=show_reviewed_files,
            rating_cfg=rating_cfg,
            previously_tracked=previously_tracked,
            summary_sink=summary_sink,
        )
        self._post_or_update_summary(
            pr_num=pr_num,
            body=summary_body,
            existing_summary=existing_summary,
            scanned=scanned,
            revision=revision,
            total_findings=total_findings,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

        if failed:
            if getattr(self._adapter, "comment_limit_reached", False):
                print(
                    f"[revue] ❌ {pr_label} #{pr_num} has reached Bitbucket's 200-comment limit "
                    f"— resolve or delete old Revue comments to make room for new ones"
                )
            else:
                print(
                    f"[revue] ❌ {failed} comment(s) could not be posted to {pr_label} #{pr_num} "
                    f"— API error (check token permissions)"
                )
        if skipped > 0:
            print(f"[revue] Review posted to {pr_label} #{pr_num} — {posted} new, {skipped} preserved inline comment(s)")
        else:
            print(f"[revue] Review posted to {pr_label} #{pr_num} — {posted} inline comment(s)")

        return posted, failed

    # ------------------------------------------------------------------
    # Summary mode
    # ------------------------------------------------------------------

    def _post_summary_mode(
        self,
        pr_num: int,
        review_results: list,
        pr_label: str,
        fallback_mode: str,
        show_reviewed_files: bool,
        rating_cfg: dict | None,
        existing_summary,
        scanned,
        revision: int,
        repo_owner: str,
        repo_name: str,
    ) -> tuple[int, int]:
        """Post all findings as a single summary comment (not inline)."""
        total_findings: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "info": 0}
        posted = 0
        failed = 0
        file_sections: list[str] = []

        for rr in review_results:
            if rr.error or not rr.response:
                continue
            try:
                findings, _ = _parse_findings(rr.response)
                for f in findings:
                    sev = (f.get("severity") or "info").lower()
                    if sev in total_findings:
                        total_findings[sev] += 1
                posted += 1
            except Exception:
                failed += 1

        summary_body = self._build_summary_body(
            review_results=review_results,
            total_findings=total_findings,
            revision=revision,
            fallback_mode=fallback_mode,
            show_reviewed_files=show_reviewed_files,
            rating_cfg=rating_cfg,
        )
        self._post_or_update_summary(
            pr_num=pr_num,
            body=summary_body,
            existing_summary=existing_summary,
            scanned=scanned,
            revision=revision,
            total_findings=total_findings,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        print(f"[revue] Review posted to {pr_label} #{pr_num} — {posted} file(s) in summary comment")
        return posted, failed

    # ------------------------------------------------------------------
    # Comment posting helpers
    # ------------------------------------------------------------------

    def _post_or_evict_and_retry(
        self,
        pr_num: int,
        position: DiffPosition,
        body: str,
        eviction_state: list[bool],
        replacement_line_count: int = 1,
    ) -> str | None:
        """Post a review comment, evicting resolved threads once on 200-limit hit."""
        comment_id = self._adapter.post_review_comment(
            pr_id=pr_num, position=position, body=body,
            replacement_line_count=replacement_line_count,
        )
        if comment_id is not None:
            return comment_id

        if not getattr(self._adapter, "comment_limit_reached", False) or eviction_state[0]:
            return None

        eviction_state[0] = True
        evicted = self._adapter.evict_resolved_revue_comments(pr_num)
        if evicted == 0:
            return None

        print(f"[revue] 🗑️ Evicted {evicted} resolved Revue comment(s) to free up space")
        self._adapter.comment_limit_reached = False
        return self._adapter.post_review_comment(
            pr_id=pr_num, position=position, body=body,
            replacement_line_count=replacement_line_count,
        )

    def _resolve_position(
        self, file_path: str, line_number: int, diff_content: str
    ) -> DiffPosition:
        """Resolve diff position via platform-specific resolver registry."""
        resolver = _POSITION_RESOLVERS.get(self._platform_str)
        if resolver is not None:
            return resolver(self._adapter, file_path, line_number, diff_content)
        return self._adapter.resolve_position(file_path, line_number, diff_content)

    # ------------------------------------------------------------------
    # Summary helpers
    # ------------------------------------------------------------------

    def _scan_for_existing_summary(self, pr_num: int) -> Optional[tuple[str, int]]:
        """Scan live platform comments for a Revue summary (AC11)."""
        try:
            get_issue_fn = getattr(self._adapter, "get_issue_comments", None)
            if callable(get_issue_fn):
                for c in get_issue_fn(pr_id=pr_num):
                    body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                    if _REVUE_SUMMARY_MARKER in body:
                        m = _REVISION_RE.search(body)
                        return (str(c.get("id", "")), int(m.group(1)) if m else 1)
            for c in self._adapter.get_existing_comments(pr_id=pr_num):
                body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                if _REVUE_SUMMARY_MARKER in body:
                    m = _REVISION_RE.search(body)
                    return (str(c.get("id", "")), int(m.group(1)) if m else 1)
        except Exception:
            pass
        return None

    def _post_or_update_summary(
        self,
        pr_num: int,
        body: str,
        existing_summary,
        scanned: Optional[tuple[str, int]],
        revision: int,
        total_findings: dict[str, int],
        repo_owner: str,
        repo_name: str,
    ) -> None:
        """Update an existing summary comment or post a new one."""
        now = datetime.now(timezone.utc)
        existing_comment_id = (
            existing_summary.platform_comment_id if existing_summary
            else (scanned[0] if scanned else None)
        )

        if existing_comment_id:
            ok = self._adapter.update_comment(
                pr_id=pr_num, comment_id=existing_comment_id, body=body
            )
            if ok:
                created_at = existing_summary.created_at if existing_summary else now
                updated = SummaryComment(
                    id=None, platform=self._platform_enum,
                    platform_comment_id=existing_comment_id,
                    pr_number=pr_num, repo_owner=repo_owner, repo_name=repo_name,
                    total_issues=sum(total_findings.values()), fixed_count=0,
                    discussed_count=0, remaining_count=sum(total_findings.values()),
                    last_updated_at=now, created_at=created_at, revision=revision,
                )
                self._summary_store.create_or_update_summary(updated)
                print(f"[revue] Summary comment updated in-place (Review #{revision})")
                return
            # Fall through to post new if update fails
            revision = revision + 1

        comment_id = self._adapter.post_summary_comment(pr_id=pr_num, body=body)
        if comment_id:
            summary = SummaryComment(
                id=None, platform=self._platform_enum,
                platform_comment_id=comment_id, pr_number=pr_num,
                repo_owner=repo_owner, repo_name=repo_name,
                total_issues=sum(total_findings.values()), fixed_count=0,
                discussed_count=0, remaining_count=sum(total_findings.values()),
                last_updated_at=now, created_at=now, revision=revision,
            )
            self._summary_store.create_or_update_summary(summary)
        else:
            print("Warning: Failed to post review summary", file=sys.stderr)

    def _build_summary_body(
        self,
        review_results: list,
        total_findings: dict[str, int],
        revision: int,
        fallback_mode: str = "normal",
        show_reviewed_files: bool = True,
        rating_cfg: dict | None = None,
        previously_tracked: int = 0,
        summary_sink: list | None = None,
    ) -> str:
        """Build summary comment body (delegates to summary_builder.build_enhanced_summary)."""
        from revue.comments.summary_builder import build_enhanced_summary
        try:
            return build_enhanced_summary(
                review_results=review_results,
                total_findings=total_findings,
                revision=revision,
                last_updated_at="just now",
                fallback_mode=fallback_mode,
                show_reviewed_files=show_reviewed_files,
                rating_cfg=rating_cfg,
                previously_tracked=previously_tracked,
                summary_sink=summary_sink or [],
            )
        except Exception as exc:
            Log.cli.warning("build_enhanced_summary failed: %s", exc)
            total = sum(total_findings.values())
            return f"{_REVUE_SUMMARY_MARKER}\n\nReview #{revision} — {total} finding(s) found."

    # ------------------------------------------------------------------
    # API fingerprint seeding
    # ------------------------------------------------------------------

    def _build_api_fingerprint_map(self, pr_num: int) -> dict[str, dict]:
        """Scan live PR comments for fingerprint sentinels and location-based fingerprints."""
        result: dict[str, dict] = {}
        try:
            comments = self._adapter.get_existing_comments(pr_id=pr_num)
        except Exception as exc:
            Log.cli.warning("get_existing_comments failed for PR %s: %s", pr_num, exc)
            return result
        for c in comments:
            try:
                body = c.get("content", {}).get("raw", "") or c.get("body", "") or ""
                effective_id = str(c.get("_discussion_id", "") or c.get("id", ""))
                resolved = bool(c.get("_discussion_resolved", False))
                _apply_sentinel_strategy(body, effective_id, result, resolved=resolved)
                _apply_location_strategy(c, body, effective_id, result, gen_fingerprint)
            except Exception as exc:
                Log.cli.warning("fingerprint scan failed for comment %s: %s", c.get("id", "?"), exc)
        return result

    # ------------------------------------------------------------------
    # Comment body building
    # ------------------------------------------------------------------

    def _build_comment_body(
        self,
        file_path: str,
        line: int,
        group_items: list[tuple],
        fp: str,
        replacement_line_count: int,
    ) -> tuple[str, int]:
        """Build comment body for a single or grouped finding."""
        from revue.core.agent_loader import filter_code_replacement

        if len(group_items) == 1:
            sev, issue, rec, details, cat, f = group_items[0]
            agent_name = f.get("agent_name") or "unknown"
            code_replacement = filter_code_replacement(f.get("code_replacement"))
            replacement_line_count = f.get("replacement_line_count", 1)
            synthesised_from = f.get("synthesised_from")
            if synthesised_from:
                attribution = [Attribution(agent_name=a[0], category=a[1]) for a in synthesised_from]
            else:
                attribution = [Attribution(agent_name=agent_name, category=cat or "general")]
            consolidated = ConsolidatedFinding(
                file_path=file_path, line_number=line,
                severity=sev,  # type: ignore[arg-type]
                issue=issue or "Issue found", suggestion=rec or "",
                confidence=float(f.get("confidence", 0.8)), category=cat or "general",
                attribution=attribution, code_replacement=code_replacement,
                replacement_line_count=replacement_line_count, snippet="",
            )
            body = self._builder.build(consolidated, fp=fp, platform=self._platform_str)
            return body, replacement_line_count

        # Multiple findings on same line
        grouped: list[ConsolidatedFinding] = []
        for sev, issue, rec, details, cat, f in group_items:
            agent_name = f.get("agent_name") or "unknown"
            item_code_replacement = filter_code_replacement(f.get("code_replacement"))
            grouped.append(ConsolidatedFinding(
                file_path=file_path, line_number=line,
                severity=sev,  # type: ignore[arg-type]
                issue=issue or "Issue found", suggestion=rec or "",
                confidence=float(f.get("confidence", 0.8)), category=cat or "general",
                attribution=[Attribution(agent_name=agent_name, category=cat or "general")],
                code_replacement=item_code_replacement,
                replacement_line_count=f.get("replacement_line_count", 1), snippet="",
                group_type="same_line",
            ))
        body = self._builder.build_grouped(grouped, fp=fp, platform=self._platform_str)
        return body, 1


# ---------------------------------------------------------------------------
# Fingerprint strategy helpers (migrated from cli.py)
# ---------------------------------------------------------------------------

_FINDING_HEADER_RE = re.compile(r"^\*\*(?:🔴|🟡|🔵|ℹ️)\s*\[(?:HIGH|MEDIUM|LOW|INFO)\]")


_CANONICAL_FP_RE = re.compile(r'^[a-f0-9]{16}$')


def _apply_sentinel_strategy(
    body: str, comment_id_str: str, result: dict, resolved: bool = False
) -> None:
    """Extract fingerprint sentinel from comment body."""
    m = _FP_SENTINEL_RE.search(body)
    if m:
        fp = m.group(1)
        if not _CANONICAL_FP_RE.match(fp):
            return
        sev_m = _FINDING_SEV_RE.search(body)
        result[fp] = {
            "platform_comment_id": comment_id_str,
            "file_path": "",
            "resolved": resolved,
            "severity": sev_m.group(1).lower() if sev_m else "",
        }


def _apply_location_strategy(
    comment: dict,
    body: str,
    comment_id_str: str,
    result: dict,
    gen_fp,
) -> None:
    """Derive location-based fingerprint from inline comment metadata."""
    if not _FINDING_HEADER_RE.match(body):
        return
    inline = comment.get("inline") or {}
    pos_raw = comment.get("position")
    position = pos_raw if isinstance(pos_raw, dict) else {}
    file_path = inline.get("path") or position.get("new_path") or comment.get("path", "")
    line = inline.get("to") or position.get("new_line") or comment.get("line") or 0
    if file_path and line:
        fp = gen_fp(file_path, int(line), "")
        if not _CANONICAL_FP_RE.match(fp):
            return
        sev_m = _FINDING_SEV_RE.search(body)
        result[fp] = {
            "platform_comment_id": comment_id_str,
            "file_path": file_path,
            "resolved": bool(comment.get("_discussion_resolved", False)),
            "severity": sev_m.group(1).lower() if sev_m else "",
        }


# ---------------------------------------------------------------------------
# Review response parsing helpers (duplicated from cli.py to avoid coupling)
# ---------------------------------------------------------------------------


def _parse_findings(response: str) -> tuple[list, str]:
    """Parse findings list from a JSON review response. Returns (findings, summary)."""
    clean = response.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.split("\n")[:-1])
    data = json.loads(clean.strip())
    if "review" in data and isinstance(data["review"], dict):
        data = data["review"]
    # Handle both list and dict formats
    if isinstance(data, list):
        return data, ""
    findings = data.get("findings", [])
    summary = data.get("summary", "") or data.get("message", "")
    return findings, summary


def _extract_finding_fields(f: dict) -> tuple[str, str, str, str, str, int]:
    """Extract and normalise fields from a finding dict. Returns (sev, issue, details, rec, cat, line)."""
    sev = (f.get("severity") or "info").lower()
    issue = (f.get("issue") or f.get("message") or f.get("title") or "").strip()
    details = (f.get("details") or f.get("description") or f.get("detail") or "").strip()
    rec = (f.get("recommendation") or f.get("suggestion") or f.get("fix") or "").strip()
    cat = (f.get("category") or f.get("type") or "").strip()
    _raw_line = f.get("line") or f.get("lines") or f.get("line_number") or 1
    try:
        line = int(_raw_line)
    except (ValueError, TypeError):
        line = 1
    return sev, issue, details, rec, cat, line

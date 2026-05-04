"""Pure function module for rendering consolidated findings into comment bodies.

BodyBuilder is a pure function module with no AI calls, no VCS I/O.
All rendering logic is testable without mocks.

Architecture spec: docs/architecture/comment-posting.md
"""
from __future__ import annotations

from typing import Callable, Literal

from .models import ConsolidatedFinding

# Severity emoji mapping
_SEVERITY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🔵",
    "info": "ℹ️",
}

_SEVERITY_ORDER = ["high", "medium", "low", "info"]


def _highest_severity(items: list[ConsolidatedFinding]) -> str:
    severities = {item.severity for item in items}
    for sev in _SEVERITY_ORDER:
        if sev in severities:
            return sev
    return "info"

# Agent display names for attribution
_AGENT_DISPLAY_NAMES = {
    "leo": "Leo",
    "zara": "Zara",
    "kai": "Kai",
    "maya": "Maya",
    "nova": "Nova",
}


def _github_suggestion_block(code_lines: list[str], replacement_line_count: int = 1) -> str:
    """GitHub native suggestion block (```suggestion fence)."""
    return "\n```suggestion\n" + "\n".join(code_lines) + "\n```"


def _gitlab_suggestion_block(code_lines: list[str], replacement_line_count: int = 1) -> str:
    """GitLab native suggestion block with line count directive."""
    lines_to_delete = max(0, replacement_line_count - 1)
    return f"\n```suggestion:-0+{lines_to_delete}\n" + "\n".join(code_lines) + "\n```"


def _bitbucket_code_block(code_lines: list[str], replacement_line_count: int = 1) -> str:
    """Bitbucket inline code block (no suggestion directive)."""
    return "\n```\n" + "\n".join(code_lines) + "\n```"


_PLATFORM_FORMATTERS: dict[str, Callable[[list[str], int], str]] = {
    "github": _github_suggestion_block,
    "gitlab": _gitlab_suggestion_block,
    "bitbucket": _bitbucket_code_block,
}


class BodyBuilder:
    """Renders ConsolidatedFinding objects into platform-specific comment body strings.

    Per-platform kind-switching: GitHub suggestion blocks, GitLab suggestions, Bitbucket prose.
    Reads summary_sink for the PR-level summary comment (Decision 6).
    """

    def build(
        self,
        finding: ConsolidatedFinding,
        fp: str,
        platform: Literal["github", "gitlab", "bitbucket"] = "github",
    ) -> str:
        """Render a single consolidated finding as a comment body.

        Args:
            finding: The finding to render.
            fp: Pre-computed fingerprint string (embedded as [//]: # (revue:fp:{fp})).
            platform: Target VCS platform — determines suggestion fence format.

        Returns:
            Comment body string with severity badge, issue, suggestion, optional code fence,
            brand footer, and fingerprint sentinel.
        """
        parts = []

        # Severity badge + issue header
        emoji = _SEVERITY_EMOJI.get(finding.severity, "⚪")
        parts.append(f"**{emoji} [{finding.severity.upper()}] {finding.issue}**")

        # Attribution header(s) — render all agents regardless of group_type
        for attr in finding.attribution:
            agent_display = _AGENT_DISPLAY_NAMES.get(attr.agent_name, attr.agent_name.title())
            category_display = attr.category.replace("-", " ").title()
            parts.append(f"*{agent_display} · {category_display}*")

        # Suggestion with vocabulary label
        if finding.suggestion:
            vocab_label = self._compute_vocabulary_label(finding.severity, finding.code_replacement is not None)
            parts.append(f"{vocab_label} {finding.suggestion}")

        # Code fence if applicable
        if finding.code_replacement:
            formatter = _PLATFORM_FORMATTERS.get(platform, _bitbucket_code_block)
            fence = formatter(finding.code_replacement, finding.replacement_line_count)
            parts.append(fence.lstrip())

        # Brand footer + fingerprint sentinel
        body = "\n".join(parts)
        body += f"\n\n— 🤖 Revue"
        body += f"\n[//]: # (revue:fp:{fp})"

        return body

    def build_grouped(
        self,
        items: list[ConsolidatedFinding],
        fp: str,
        platform: Literal["github", "gitlab", "bitbucket"] = "github",
    ) -> str:
        """Render multiple same-line findings as a single merged comment body.

        Args:
            items: Two or more findings that share a line (or are close neighbours).
            fp: Pre-computed fingerprint string to embed.
            platform: Target VCS platform — determines suggestion fence format.

        Returns:
            Comment body with a count header, one block per finding, brand footer,
            and fingerprint sentinel.
        """
        parts = []

        # Count header with highest severity badge — first line
        n = len(items)
        top_sev = _highest_severity(items)
        emoji = _SEVERITY_EMOJI.get(top_sev, "⚪")
        parts.append(f"**{emoji} [{top_sev.upper()}] {n} findings on this line**")

        # Per-item block
        for item in items:
            emoji = _SEVERITY_EMOJI.get(item.severity, "⚪")
            parts.append(f"\n---\n{emoji} **[{item.severity.upper()}] {item.issue}**")
            # Render all attribution entries for this finding
            for attr in item.attribution:
                agent_display = _AGENT_DISPLAY_NAMES.get(attr.agent_name, attr.agent_name.title())
                category_display = attr.category.replace("-", " ").title()
                parts.append(f"*{agent_display} · {category_display}*")

            if item.suggestion:
                vocab_label = self._compute_vocabulary_label(item.severity, item.code_replacement is not None)
                parts.append(f"{vocab_label} {item.suggestion}")

            if item.code_replacement:
                formatter = _PLATFORM_FORMATTERS.get(platform, _bitbucket_code_block)
                fence = formatter(item.code_replacement, item.replacement_line_count)
                parts.append(fence.lstrip())

        body = "\n".join(parts)
        body += f"\n\n— 🤖 Revue"
        body += f"\n[//]: # (revue:fp:{fp})"
        return body

    @staticmethod
    def build_summary(
        findings: list[ConsolidatedFinding],
        summary_sink: list[ConsolidatedFinding],
    ) -> str:
        """Render PR-level summary comment.

        Args:
            findings: All findings (for severity counts and grouping).
            summary_sink: Unanchored findings (rendered in a separate section).

        Returns:
            Summary comment body string with severity table and optional unanchored section.
        """
        parts = []

        # Count findings by severity
        severity_counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
        for finding in findings:
            if finding.severity in severity_counts:
                severity_counts[finding.severity] += 1

        # Severity summary table
        parts.append("## Findings Summary")
        parts.append("")
        severity_order = ["high", "medium", "low", "info"]
        severity_lines = []
        for severity in severity_order:
            count = severity_counts[severity]
            if count > 0:
                emoji = _SEVERITY_EMOJI.get(severity, "⚪")
                severity_lines.append(f"| {emoji} **{severity.title()}** | {count} |")

        if severity_lines:
            parts.append("| Severity | Count |")
            parts.append("|----------|-------|")
            parts.extend(severity_lines)
            parts.append("")

        # Unanchored findings section
        if summary_sink:
            parts.append("### Unanchored Findings")
            parts.append("")
            for finding in summary_sink:
                emoji = _SEVERITY_EMOJI.get(finding.severity, "⚪")
                parts.append(f"- {emoji} **{finding.issue}**")
                if finding.suggestion:
                    parts.append(f"  - {finding.suggestion}")
            parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _compute_vocabulary_label(severity: str, has_code_replacement: bool) -> str:
        """Derive vocabulary label based on severity and code_replacement presence.

        Rules:
        - severity == "info" → "ℹ️ Note:"
        - code_replacement is not None (any other severity) → "💡 Action:"
        - else → "💡 Suggest:"
        """
        if severity == "info":
            return "> ℹ️ **Note:**"
        elif has_code_replacement:
            return "> 💡 **Action:**"
        else:
            return "> 💡 **Suggest:**"

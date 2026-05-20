"""Summary comment body construction (REVUE-211).

Extracted from cli.py so Poster can import it without inverting the
cli → service layer hierarchy. cli.py re-imports from here.
"""
from __future__ import annotations

import json

from revue_core.comments.body_builder import BodyBuilder

# ---------------------------------------------------------------------------
# Display constants — canonical maps live in core/display.py; re-exported
# here under the legacy names so existing imports keep working.
# ---------------------------------------------------------------------------

from revue_core.core.display import (
    SEVERITY_EMOJIS as SEVERITY_EMOJI,
    SEVERITY_ORDER,
)

_CATEGORY_MAP: dict[str, str] = {
    "architecture": "Architecture",
    "security": "Security",
    "performance": "Performance",
    "code-quality": "Code Quality",
}
_CATEGORY_CLEAN_LABELS: dict[str, str] = {
    "Architecture": "SOLID compliant, no structural issues",
    "Security": "No vulnerabilities detected",
    "Performance": "No blocking issues",
    "Code Quality": "All patterns followed",
}
# Display maps live in ._agent_display so body_builder.py can read them
# without creating an import cycle (summary_builder imports BodyBuilder).
# Re-exported under the legacy underscore-prefixed names so cli.py and
# pipeline.py keep working without churn.
from ._agent_display import (
    AGENT_DISPLAY_NAMES as _AGENT_DISPLAY_NAMES,
    AGENT_EMOJIS as _AGENT_EMOJIS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _star_rating(
    total: int,
    high: int,
    medium: int,
    low: int = 0,
    info: int = 0,
    rating_cfg: dict | None = None,
) -> str:
    """Return a star rating string (1–5) based on finding severity counts."""
    if total == 0:
        return "⭐⭐⭐⭐⭐ 5.0/5.0"
    cfg = rating_cfg or {}
    w_high   = float(cfg.get("high",   1.5))
    w_medium = float(cfg.get("medium", 0.3))
    w_low    = float(cfg.get("low",    0.05))
    w_info   = float(cfg.get("info",   0.0))
    floor    = float(cfg.get("floor",  1.0))
    score = 5.0 - (high * w_high + medium * w_medium + low * w_low + info * w_info)
    score = max(floor, min(5.0, score))
    full = int(score)
    half = 1 if (score - full) >= 0.5 else 0
    empty = 5 - full - half
    stars = "⭐" * full + ("✨" if half else "") + "☆" * empty
    return f"{stars} {score:.1f}/5.0"


def _parse_findings_for_summary(response: str) -> tuple[list, str]:
    """Parse findings from a JSON review response. Returns (findings, summary)."""
    clean = response.strip()
    if clean.startswith("```"):
        clean = "\n".join(clean.split("\n")[1:])
    if clean.endswith("```"):
        clean = "\n".join(clean.split("\n")[:-1])
    data = json.loads(clean.strip())
    if "review" in data and isinstance(data["review"], dict):
        data = data["review"]
    if isinstance(data, list):
        return data, ""
    return data.get("findings", []), data.get("summary", "") or data.get("message", "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_enhanced_summary(
    review_results: list,
    total_findings: dict[str, int],
    revision: int,
    last_updated_at: str,
    fallback_mode: str = "normal",
    show_reviewed_files: bool = True,
    rating_cfg: dict | None = None,
    previously_tracked: int = 0,
    summary_sink: list | None = None,
) -> str:
    """Build the rich REVUE-97 summary comment body.

    Args:
        review_results:     List of ReviewResult objects from the pipeline.
        total_findings:     Dict of {severity: count} for findings requiring attention.
        revision:           Current review revision number (1 = first post).
        last_updated_at:    Human-readable relative timestamp string.
        fallback_mode:      Active fallback mode from pipeline (REVUE-117).
        previously_tracked: Won't-fix findings skipped this cycle.
        summary_sink:       Unanchored findings to render in a separate section.
    """
    total = sum(total_findings.values())
    high = total_findings.get("high", 0)
    medium = total_findings.get("medium", 0)
    low = total_findings.get("low", 0)
    info = total_findings.get("info", 0)

    if total == 0:
        verdict_icon = "✅"
        verdict_text = "Approved"
    elif high > 0:
        verdict_icon = "❌"
        verdict_text = f"{total} issue{'s' if total != 1 else ''} found"
    else:
        verdict_icon = "⚠️"
        verdict_text = f"{total} issue{'s' if total != 1 else ''} found"

    stars = _star_rating(total, high, medium, low, info, rating_cfg)

    lines = [
        f"## 🤖 Revue — Code Review (Review #{revision})",
        "",
        f"**Overall:** {stars} · {verdict_icon} {verdict_text}  ",
        f"**Last updated:** {last_updated_at}",
        "",
    ]

    if fallback_mode and fallback_mode != "normal":
        mode_display = fallback_mode.replace("_", "-")
        lines += [
            f"> ⚠️ **Reduced context mode active ({mode_display}):** This review used a "
            f"smaller diff context to avoid API rate limits. Some findings may be missing. "
            f"To restore full-context reviews, upgrade your API tier, keep PRs smaller, "
            f"or set `retry_on_rate_limit: true` in `.revue.yml`.",
            "",
        ]

    category_counts: dict[str, list] = {label: [] for label in _CATEGORY_MAP.values()}
    for rr in review_results:
        if rr.error or not rr.response:
            continue
        try:
            findings, _ = _parse_findings_for_summary(rr.response)
        except Exception:
            continue
        for f in findings:
            raw_cat = f.get("category", "").lower().strip() if isinstance(f, dict) else ""
            display = _CATEGORY_MAP.get(raw_cat, "Code Quality")
            category_counts[display].append(f)

    lines.append("### Quality Breakdown")
    for display_label in _CATEGORY_MAP.values():
        cat_findings = category_counts[display_label]
        if not cat_findings:
            lines.append(f"- ✅ **{display_label}:** {_CATEGORY_CLEAN_LABELS[display_label]}")
        else:
            by_sev: dict[str, int] = {}
            for f in cat_findings:
                s = (f.get("severity", "low").lower() if isinstance(f, dict) else "low")
                by_sev[s] = by_sev.get(s, 0) + 1
            sev_parts = " ".join(
                f"{SEVERITY_EMOJI.get(s, '⚪')} {by_sev[s]} {s}"
                for s in SEVERITY_ORDER
                if by_sev.get(s, 0) > 0
            )
            lines.append(f"- ⚠️ **{display_label}:** {sev_parts}")
    lines.append("")

    if show_reviewed_files:
        reviewed = [rr for rr in review_results if not rr.error and rr.response]
        unique_paths = list(dict.fromkeys(rr.file_path for rr in reviewed))
        lines.append(f"### Files Reviewed ({len(unique_paths)})")
        for path in unique_paths:
            lines.append(f"- `{path}`")
        lines.append("")

    if total == 0 and previously_tracked == 0:
        lines.append("### Findings: 0 issues")
        lines.append("")
        lines.append(
            "**Verdict:** Clean implementation following project standards. "
            "No issues detected across all reviewed files."
        )
    elif total == 0 and previously_tracked > 0:
        lines.append("### Findings: 0 issues")
        lines.append(f"*({previously_tracked} finding{'s' if previously_tracked != 1 else ''} previously tracked — already decided)*")
        lines.append("")
        lines.append("**Verdict:** ✅ All findings have been addressed.")
    else:
        counts_str = " · ".join(
            f"{SEVERITY_EMOJI.get(s, '⚪')} {total_findings[s]} {s}"
            for s in SEVERITY_ORDER
            if total_findings.get(s, 0) > 0
        )
        issue_word = f"issue{'s' if total != 1 else ''}"
        lines.append(f"### Findings: {total} {issue_word}")
        lines.append(f"{counts_str}")
        if previously_tracked > 0:
            lines.append(
                f"*({previously_tracked} previously tracked — won't-fix decisions already recorded)*"
            )
        lines.append("")
        lines.append(
            f"**Verdict:** {verdict_icon} {total} {issue_word} require "
            f"attention. See inline comments for details."
        )

    body = "\n".join(lines)

    if summary_sink:
        unanchored_section = BodyBuilder.build_summary(findings=[], summary_sink=summary_sink)
        body += "\n\n" + unanchored_section

    return body

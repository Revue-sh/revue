"""Post-review side-effects: upgrade banner + cost footer + usage telemetry."""
from __future__ import annotations


def emit_post_review_signals(
	findings_count: int, invocation_ts: int, no_footer: bool = False
) -> None:
	"""Run the best-effort, post-review side-effects shared by every
	review-completion path:
	1. Update local usage cache (for cost footer)
	2. Free-tier upgrade banner (if cache reports exhausted)
	3. Cost-saving footer (shows savings from using Revue vs CI-only)
	4. Usage-telemetry emit to server

	Args:
		findings_count: Number of findings in this review
		invocation_ts: Unix timestamp when the review began
		no_footer: If True, suppress the cost-saving footer (for piped/CI
			usage). Passed directly into ``render_cost_footer`` — no env
			mutation, no process-wide state leak.

	All are best-effort and never block the review — that contract is owned
	by the called functions; this helper only fixes the call order so the
	user sees all messages in the right order.
	"""
	from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
	from revue_skill.skill.cost_footer import render_cost_footer
	from revue_skill.skill.emit_usage import emit_usage
	from revue_skill.skill.update_usage_cache import update_usage_cache

	update_usage_cache()
	render_upgrade_prompt_if_exhausted()
	render_cost_footer(no_footer=no_footer)
	emit_usage(findings_count=findings_count, emitted_at=invocation_ts)
	# REVUE-364: funnel review event. Separate from emit_usage (billing) — gated
	# by REVUE_TELEMETRY_OFF; billing counter always fires regardless of opt-out.
	from revue_skill.funnel_telemetry import emit_funnel_event
	emit_funnel_event("review")

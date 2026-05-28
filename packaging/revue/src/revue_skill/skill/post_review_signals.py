"""Post-review side-effects: upgrade banner + usage telemetry."""
from __future__ import annotations


def emit_post_review_signals(findings_count: int, invocation_ts: int) -> None:
	"""Run the two best-effort, post-review side-effects shared by every
	review-completion path: the free-tier upgrade banner (if cache reports
	exhausted) and the usage-telemetry emit. Both are best-effort and never
	block the review — that contract is owned by the called functions; this
	helper only fixes the call order (banner before telemetry so the user
	sees the prompt even if the network is slow).
	"""
	from revue_skill.skill.upgrade_prompt import render_upgrade_prompt_if_exhausted
	from revue_skill.skill.emit_usage import emit_usage

	render_upgrade_prompt_if_exhausted()
	emit_usage(findings_count=findings_count, emitted_at=invocation_ts)

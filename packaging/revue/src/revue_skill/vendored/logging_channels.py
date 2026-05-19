"""Bootstrap logging channels for Revue.

Registers named channels at module import time. Agent-specific channels
(nova) pull their emoji from ``core.display.AGENT_EMOJIS`` so identity
stays consistent everywhere; the non-agent channels keep their own
operational glyphs (🔧 pipeline, 🤖 agent runner, 💻 CLI, 📍 position).

All channels default to INFO level.

Also hosts cross-adapter logging convention helpers — keep these here so
new adapters don't drift to ad-hoc log formats.
"""

import logging

from revue_skill.vendored.display import AGENT_EMOJIS
from revue_skill.vendored.log import Log

# Register the Revue channels at module import time.
Log.register("pipeline", "🔧", logging.INFO)
Log.register("agent", "🤖", logging.INFO)
Log.register("nova", AGENT_EMOJIS["nova"], logging.INFO)
Log.register("cli", "💻", logging.INFO)
Log.register("position", "📍", logging.INFO)


def log_comment_posted(
    *, platform: str, pr_id: int, comment_id: str | None, api_params: dict,
) -> None:
    """Single grep-friendly success log for inline comment posting (REVUE-238).

    All three VCS adapters (GitHub, GitLab, Bitbucket) emit this identically
    on a successful ``post_review_comment_with_params`` call. Centralised so
    monitoring greps for one stable line shape across all platforms.
    """
    Log.cli.info(
        "✅ post_review_comment_with_params: platform=%s pr_id=%s comment_id=%s api_params=%s",
        platform, pr_id, comment_id, api_params,
    )

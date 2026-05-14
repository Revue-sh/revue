"""Bootstrap logging channels for Revue.

Registers named channels at module import time. Agent-specific channels
(nova) pull their emoji from ``core.display.AGENT_EMOJIS`` so identity
stays consistent everywhere; the non-agent channels keep their own
operational glyphs (🔧 pipeline, 🤖 agent runner, 💻 CLI, 📍 position).

All channels default to INFO level.
"""

import logging

from revue.core.display import AGENT_EMOJIS
from revue.core.log import Log

# Register the Revue channels at module import time.
Log.register("pipeline", "🔧", logging.INFO)
Log.register("agent", "🤖", logging.INFO)
Log.register("nova", AGENT_EMOJIS["nova"], logging.INFO)
Log.register("cli", "💻", logging.INFO)
Log.register("position", "📍", logging.INFO)

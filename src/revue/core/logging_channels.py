"""Bootstrap logging channels for Revue.

Registers four named channels at module import time:
- pipeline (🔧) — pipeline orchestration & flow control
- agent (🤖) — agent execution (Cleo, Zara, etc.)
- nova (✨) — Nova consolidation & synthesis
- cli (💻) — CLI interface & user-facing output

All channels default to INFO level.
"""

import logging

from revue.core.log import Log

# Register the four Revue channels at module import time
Log.register("pipeline", "🔧", logging.INFO)
Log.register("agent", "🤖", logging.INFO)
Log.register("nova", "✨", logging.INFO)
Log.register("cli", "💻", logging.INFO)

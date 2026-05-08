#!/usr/bin/env python3
"""Local sandbox entry point for manual testing of RevueLogger.

Demonstrates channel setup with a proxy hook that prints to stdout.
No context-manager kludge — straightforward wire-up.
"""

import sys
import os

# Add src to path so we can import revue modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from revue.core.logging_channels import Log
from revue.core.log import RevueLogger


def main():
    """Set up logging and run a simple test."""
    # Set up proxy hook to print all messages to stdout
    RevueLogger.shared().setup(on_log=print)

    # Log some test messages at different levels
    Log.pipeline.verbose("This is a VERBOSE message (transport debugging)")
    Log.pipeline.debug("This is a DEBUG message (feature debugging)")
    Log.pipeline.info("This is an INFO message (operational progress)")
    Log.pipeline.warning("This is a WARNING message (degradation watch)")
    Log.pipeline.error("This is an ERROR message (alert/triage)")

    # Test other channels
    print("\n--- Agent Channel ---")
    Log.agent.info("Agent started")
    Log.agent.warning("Agent warning")

    print("\n--- Nova Channel ---")
    Log.nova.info("Nova synthesis started")
    Log.nova.debug("Nova debug info")

    print("\n--- CLI Channel ---")
    Log.cli.info("CLI command executed")
    Log.cli.warning("CLI warning message")

    print("\nLocal run complete!")


if __name__ == "__main__":
    main()

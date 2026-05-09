"""Tests for logging_channels.py bootstrap module."""

import importlib
import logging
import sys
import unittest
from unittest.mock import patch

from revue.core.log import Log, RevueLogger, VERBOSE


class TestLoggingChannelsBootstrap(unittest.TestCase):
    """Test that bootstrap registers four Revue channels at startup."""

    @classmethod
    def setUpClass(cls):
        """Force re-registration of channels without YAML overrides."""
        import revue.core.logging_channels as lc
        # Patch out YAML config so user's ~/.config/revue/log_channels.yaml
        # does not override code-default levels during these tests.
        with patch.object(RevueLogger.shared(), "_yaml_config", {}):
            importlib.reload(lc)

    def test_logging_channels_bootstrap_registers_four_channels(self):
        """Importing logging_channels registers pipeline, agent, nova, cli channels."""
        # Verify all four channels are registered (imported in setUpClass)
        self.assertIn("pipeline", Log._channels)
        self.assertIn("agent", Log._channels)
        self.assertIn("nova", Log._channels)
        self.assertIn("cli", Log._channels)

    def test_logging_channels_pipeline_emoji(self):
        """Pipeline channel has 🔧 emoji."""
        self.assertEqual(Log.pipeline.emoji, "🔧")

    def test_logging_channels_agent_emoji(self):
        """Agent channel has 🤖 emoji."""
        self.assertEqual(Log.agent.emoji, "🤖")

    def test_logging_channels_nova_emoji(self):
        """Nova channel has ✨ emoji."""
        self.assertEqual(Log.nova.emoji, "✨")

    def test_logging_channels_cli_emoji(self):
        """CLI channel has 💻 emoji."""
        self.assertEqual(Log.cli.emoji, "💻")

    def test_logging_channels_default_level_info(self):
        """All four channels start at INFO (20) by default."""
        self.assertEqual(Log.pipeline.level, logging.INFO)
        self.assertEqual(Log.agent.level, logging.INFO)
        self.assertEqual(Log.nova.level, logging.INFO)
        self.assertEqual(Log.cli.level, logging.INFO)


if __name__ == "__main__":
    unittest.main()

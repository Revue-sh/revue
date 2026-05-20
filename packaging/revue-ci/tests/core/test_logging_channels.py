"""Tests for logging_channels.py bootstrap module."""

import importlib
import logging
import sys
import unittest
from unittest.mock import MagicMock, patch

from revue_core.core.log import Log, RevueLogger, VERBOSE


class TestLoggingChannelsBootstrap(unittest.TestCase):
    """Test that bootstrap registers four Revue channels at startup."""

    @classmethod
    def setUpClass(cls):
        """Force re-registration of channels without YAML overrides."""
        import revue_core.core.logging_channels as lc
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
        """Nova channel emoji matches the canonical AGENT_EMOJIS map.

        Previously this asserted a hardcoded ✨ — which is actually Maya's
        emoji in the canonical map. Locking the channel to the agent map
        means re-skinning Nova propagates everywhere automatically.
        """
        from revue_core.core.display import AGENT_EMOJIS
        self.assertEqual(Log.nova.emoji, AGENT_EMOJIS["nova"])

    def test_logging_channels_cli_emoji(self):
        """CLI channel has 💻 emoji."""
        self.assertEqual(Log.cli.emoji, "💻")

    def test_logging_channels_default_level_info(self):
        """All four channels start at INFO (20) by default."""
        self.assertEqual(Log.pipeline.level, logging.INFO)
        self.assertEqual(Log.agent.level, logging.INFO)
        self.assertEqual(Log.nova.level, logging.INFO)
        self.assertEqual(Log.cli.level, logging.INFO)


class TestLogCommentPostedHelper(unittest.TestCase):
    """Test the log_comment_posted() helper — single grep-friendly success line."""

    def test_log_comment_posted_emits_consistent_format_across_platforms(self):
        """Helper emits identical-shape log for GitHub, GitLab, Bitbucket."""
        from revue_core.core.logging_channels import log_comment_posted

        with patch.object(Log, "cli") as mock_cli:
            log_comment_posted(
                platform="github", pr_id=42, comment_id="gh-c-1",
                api_params={"path": "a.py", "line": 10},
            )
            log_comment_posted(
                platform="gitlab", pr_id=42, comment_id="gl-d-1",
                api_params={"position_type": "text", "new_line": 10},
            )
            log_comment_posted(
                platform="bitbucket", pr_id=42, comment_id="bb-c-1",
                api_params={"inline": {"path": "a.py", "to": 10}},
            )

        # All three calls use Log.cli.info with the same template
        self.assertEqual(mock_cli.info.call_count, 3)
        for call in mock_cli.info.call_args_list:
            template = call.args[0]
            self.assertIn("post_review_comment_with_params", template)
            self.assertIn("platform=", template)
            self.assertIn("pr_id=", template)
            self.assertIn("comment_id=", template)
            self.assertIn("api_params=", template)

    def test_log_comment_posted_passes_correct_arguments(self):
        """Helper interpolates platform, pr_id, comment_id, api_params in order."""
        from revue_core.core.logging_channels import log_comment_posted

        with patch.object(Log, "cli") as mock_cli:
            api_params = {"inline": {"path": "f.py", "to": 5}}
            log_comment_posted(
                platform="bitbucket", pr_id=99, comment_id="abc", api_params=api_params,
            )

        call = mock_cli.info.call_args
        # Args after template should be: platform, pr_id, comment_id, api_params
        self.assertEqual(call.args[1], "bitbucket")
        self.assertEqual(call.args[2], 99)
        self.assertEqual(call.args[3], "abc")
        self.assertEqual(call.args[4], api_params)


if __name__ == "__main__":
    unittest.main()

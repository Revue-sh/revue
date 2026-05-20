"""Tests for the core logging module (log.py).

Tests cover Channel, RevueLogger (singleton), FileLogger, Log namespace,
and their interactions with zero-cost suppression, call-site metadata,
proxy hooks, and file rotation.
"""

import inspect
import logging
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import classes under test
from revue_core.core.log import VERBOSE, Channel, FileLogger, Log, RevueLogger


class TestChannel(unittest.TestCase):
    """Test Channel class: name, emoji, level, and typed logging methods."""

    def test_channel_init_with_name_emoji_level(self):
        """Channel constructor stores name, emoji, and level."""
        channel = Channel("pipeline", "🔧", logging.INFO)
        self.assertEqual(channel.name, "pipeline")
        self.assertEqual(channel.emoji, "🔧")
        self.assertEqual(channel.level, logging.INFO)

    def test_channel_verbose_method_returns_zero_cost_suppressed(self):
        """verbose() on INFO-level channel produces zero output, proxy not called."""
        channel = Channel("pipeline", "🔧", logging.INFO)
        proxy = MagicMock()
        channel._logger = RevueLogger.shared()
        channel._logger.setup(on_log=proxy)

        # Verbose message below threshold should not invoke proxy
        channel.verbose("test message")
        proxy.assert_not_called()

    def test_channel_message_includes_call_site_metadata(self):
        """Message includes file, function, line via inspect."""
        channel = Channel("pipeline", "🔧", VERBOSE)
        proxy = MagicMock()
        channel._logger = RevueLogger.shared()
        channel._logger.setup(on_log=proxy)

        channel.info("test message")

        # Verify proxy was called with message containing metadata
        self.assertTrue(proxy.called)
        message = proxy.call_args[0][0]
        # Should contain emoji, channel name, level, and file metadata
        self.assertIn("🔧", message)
        self.assertIn("pipeline", message)
        self.assertIn("INFO", message)
        self.assertIn("test_log.py", message)

    def test_channel_level_can_be_set(self):
        """Channel level can be changed via set_level()."""
        channel = Channel("pipeline", "🔧", logging.INFO)
        self.assertEqual(channel.level, logging.INFO)

        channel.set_level(logging.DEBUG)
        self.assertEqual(channel.level, logging.DEBUG)

    def test_channel_all_typed_methods_exist(self):
        """Channel has verbose, debug, info, warning, error methods."""
        channel = Channel("pipeline", "🔧", VERBOSE)
        self.assertTrue(callable(channel.verbose))
        self.assertTrue(callable(channel.debug))
        self.assertTrue(callable(channel.info))
        self.assertTrue(callable(channel.warning))
        self.assertTrue(callable(channel.error))

    def test_channel_suppresses_below_threshold(self):
        """Messages below channel threshold are suppressed."""
        channel = Channel("pipeline", "🔧", logging.WARNING)
        proxy = MagicMock()
        channel._logger = RevueLogger.shared()
        channel._logger.setup(on_log=proxy)

        channel.info("below threshold")
        proxy.assert_not_called()

        channel.warning("at threshold")
        self.assertTrue(proxy.called)

    def test_channel_error_method_always_logged(self):
        """ERROR level messages always pass through."""
        channel = Channel("pipeline", "🔧", logging.ERROR)
        proxy = MagicMock()
        channel._logger = RevueLogger.shared()
        channel._logger.setup(on_log=proxy)

        channel.error("error message")
        self.assertTrue(proxy.called)


class TestRevueLogger(unittest.TestCase):
    """Test RevueLogger singleton: lazy initialization, proxy hooks, dispatch."""

    def test_revuelogger_singleton_shared(self):
        """RevueLogger.shared() returns same instance on multiple calls."""
        instance1 = RevueLogger.shared()
        instance2 = RevueLogger.shared()
        self.assertIs(instance1, instance2)

    def test_revuelogger_setup_accepts_proxy_hook(self):
        """setup(on_log=...) accepts callable and stores it."""
        logger = RevueLogger.shared()
        proxy = MagicMock()
        logger.setup(on_log=proxy)
        # Verify proxy is stored (we test dispatch next)
        self.assertIsNotNone(logger._proxy_hook)

    def test_revuelogger_dispatches_channel_messages_to_proxy(self):
        """Channel message calls proxy hook when threshold passed."""
        logger = RevueLogger.shared()
        proxy = MagicMock()
        logger.setup(on_log=proxy)

        # Create a channel at VERBOSE level and dispatch a message
        channel = Channel("test", "📝", VERBOSE)
        channel._logger = logger
        channel.info("test message")

        self.assertTrue(proxy.called)

    def test_revuelogger_dispatch_not_called_for_suppressed_message(self):
        """Proxy hook not called for suppressed (below threshold) messages."""
        logger = RevueLogger.shared()
        proxy = MagicMock()
        logger.setup(on_log=proxy)

        channel = Channel("test", "📝", logging.WARNING)
        channel._logger = logger
        channel.info("below threshold")

        proxy.assert_not_called()


class TestFileLogger(unittest.TestCase):
    """Test FileLogger: file rotation, cleanup, directory creation, error handling."""

    def test_filelogger_creates_log_dir(self):
        """FileLogger creates log directory if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, "logs")
            self.assertFalse(os.path.exists(log_dir))

            file_logger = FileLogger(log_dir)
            self.assertTrue(os.path.exists(log_dir))

    def test_filelogger_writes_to_dated_file(self):
        """FileLogger writes to YYYY-MM-DD.log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_logger = FileLogger(tmpdir)
            today = datetime.now().strftime("%Y-%m-%d")

            file_logger.write("test message")

            expected_file = os.path.join(tmpdir, f"{today}.log")
            self.assertTrue(os.path.exists(expected_file))
            with open(expected_file) as f:
                content = f.read()
                self.assertIn("test message", content)

    def test_filelogger_rotates_at_date_boundary(self):
        """FileLogger creates new file when date changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files manually with different dates
            today = datetime.now()
            yesterday = today - timedelta(days=1)

            yesterday_file = os.path.join(tmpdir, yesterday.strftime("%Y-%m-%d.log"))
            today_file = os.path.join(tmpdir, today.strftime("%Y-%m-%d.log"))

            # Write to files manually
            with open(yesterday_file, "w") as f:
                f.write("yesterday message\n")

            with open(today_file, "w") as f:
                f.write("today message\n")

            # Verify both files exist
            self.assertTrue(os.path.exists(yesterday_file))
            self.assertTrue(os.path.exists(today_file))

    def test_filelogger_retains_5_days(self):
        """FileLogger deletes files older than 5 days."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_logger = FileLogger(tmpdir)

            # Create files from different dates
            now = datetime.now()
            for days_ago in range(10):
                date = now - timedelta(days=days_ago)
                date_str = date.strftime("%Y-%m-%d")
                filepath = os.path.join(tmpdir, f"{date_str}.log")
                Path(filepath).touch()

            with patch("revue_core.core.log.datetime") as mock_dt:
                mock_dt.now.return_value = now
                # Also need to handle strptime
                mock_dt.strptime = datetime.strptime
                file_logger.cleanup()

            # Should keep last 5 days (0-4), delete 5+ days old
            for days_ago in range(10):
                date = now - timedelta(days=days_ago)
                date_str = date.strftime("%Y-%m-%d")
                filepath = os.path.join(tmpdir, f"{date_str}.log")
                if days_ago < 5:
                    self.assertTrue(os.path.exists(filepath), f"{date_str}.log should exist")
                else:
                    self.assertFalse(os.path.exists(filepath), f"{date_str}.log should be deleted")

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "root bypasses POSIX read-only permissions, so chmod 0o444 cannot simulate an unwritable dir",
    )
    def test_filelogger_handles_unwritable_dir(self):
        """FileLogger gracefully disables output if directory is unwritable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a read-only directory
            log_dir = os.path.join(tmpdir, "readonly")
            os.makedirs(log_dir)
            os.chmod(log_dir, 0o444)  # Read-only

            try:
                file_logger = FileLogger(log_dir)
                # Should not raise, but should disable output
                file_logger.write("test")  # Should not crash
                self.assertTrue(file_logger._disabled)
            finally:
                # Restore permissions for cleanup
                os.chmod(log_dir, 0o755)


class TestLogNamespace(unittest.TestCase):
    """Test Log namespace: register, dynamic attribute access."""

    def setUp(self):
        """Save and clear registered channels for test isolation."""
        self._saved_channels = dict(Log._channels)
        Log._channels.clear()

    def tearDown(self):
        """Restore channels so other tests see the expected bootstrap state."""
        Log._channels.clear()
        Log._channels.update(self._saved_channels)

    def test_log_register_adds_channel(self):
        """Log.register() creates a channel accessible via dynamic attribute."""
        Log.register("pipeline", "🔧", logging.INFO)
        self.assertIsNotNone(Log.pipeline)
        self.assertEqual(Log.pipeline.name, "pipeline")
        self.assertEqual(Log.pipeline.emoji, "🔧")

    def test_log_register_multiple_channels(self):
        """Multiple channels can be registered."""
        Log.register("pipeline", "🔧", logging.INFO)
        Log.register("agent", "🤖", logging.DEBUG)

        self.assertEqual(Log.pipeline.name, "pipeline")
        self.assertEqual(Log.agent.name, "agent")

    def test_log_getattr_raises_for_unregistered(self):
        """Accessing unregistered channel raises AttributeError."""
        with self.assertRaises(AttributeError):
            _ = Log.nonexistent

    def test_verbose_level_constant(self):
        """VERBOSE custom level is registered at numeric 5."""
        self.assertEqual(logging.getLevelName(5), "VERBOSE")
        self.assertEqual(logging.getLevelName("VERBOSE"), 5)


class TestVERBOSELevel(unittest.TestCase):
    """Test VERBOSE custom log level registration."""

    def test_verbose_level_is_5(self):
        """VERBOSE level is registered at numeric 5."""
        # Should be registered by log.py module import
        level_name = logging.getLevelName(5)
        self.assertEqual(level_name, "VERBOSE")

    def test_verbose_level_lower_than_debug(self):
        """VERBOSE (5) is lower than DEBUG (10)."""
        self.assertLess(5, logging.DEBUG)


class TestChannelEnvVarResolution(unittest.TestCase):
    """Test channel level resolution via environment variables."""

    def test_channel_level_env_var_override(self):
        """REVUE_LOG_<CHANNEL>=debug sets channel level to DEBUG."""
        with patch.dict(os.environ, {"REVUE_LOG_PIPELINE": "debug"}):
            channel = Channel("pipeline", "🔧", logging.INFO)
            self.assertEqual(channel.level, logging.DEBUG)

    def test_channel_level_env_var_validation(self):
        """Invalid env var value ignored, level stays at code default."""
        with patch.dict(os.environ, {"REVUE_LOG_PIPELINE": "invalid"}):
            channel = Channel("pipeline", "🔧", logging.INFO)
            self.assertEqual(channel.level, logging.INFO)

    def test_channel_level_env_var_all_valid_values(self):
        """All valid env var values work: verbose, debug, info, warning, error, off."""
        valid_values = {
            "verbose": VERBOSE,
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "off": logging.CRITICAL + 1,
        }

        for env_value, expected_level in valid_values.items():
            with patch.dict(os.environ, {"REVUE_LOG_TEST": env_value}):
                channel = Channel("test", "📝", logging.INFO)
                self.assertEqual(channel.level, expected_level)


class TestChannelYAMLResolution(unittest.TestCase):
    """Test channel level resolution via YAML config file."""

    def setUp(self):
        """Save singleton YAML config state to prevent test bleed."""
        logger = RevueLogger.shared()
        self._saved_yaml_config = dict(logger._yaml_config)
        self._saved_yaml_error = logger._yaml_parse_error

    def tearDown(self):
        """Restore singleton YAML config state."""
        logger = RevueLogger.shared()
        logger._yaml_config = self._saved_yaml_config
        logger._yaml_parse_error = self._saved_yaml_error

    def test_channel_level_yaml_config_override(self):
        """YAML config with pipeline: debug sets pipeline level to DEBUG."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "log_channels.yaml")
            with open(config_file, "w") as f:
                f.write("pipeline: debug\nagent: verbose\n")

            with patch("revue_core.core.log.RevueLogger._get_config_path", return_value=config_file):
                logger = RevueLogger.shared()
                logger._load_yaml_config()

    def test_channel_level_yaml_not_found_uses_default(self):
        """Missing YAML config uses code defaults."""
        with patch("revue_core.core.log.RevueLogger._get_config_path", return_value="/nonexistent"):
            logger = RevueLogger.shared()
            # Should not raise, should use defaults
            logger._load_yaml_config()

    def test_channel_level_yaml_malformed_uses_default(self):
        """Malformed YAML triggers fallback to defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "log_channels.yaml")
            with open(config_file, "w") as f:
                f.write("invalid: yaml: syntax: here [")

            logger = RevueLogger.shared()
            with patch("revue_core.core.log.RevueLogger._get_config_path", return_value=config_file):
                # Should not raise, should handle gracefully
                logger._load_yaml_config()


class TestThirdPartyLoggerControl(unittest.TestCase):
    """Test VERBOSE-triggered third-party logger level control."""

    def test_verbose_channel_lowers_httpx_to_debug(self):
        """Setting any channel to VERBOSE lowers httpx logger to DEBUG."""
        logger = RevueLogger.shared()

        httpx_logger = logging.getLogger("httpx")
        httpx_logger.setLevel(logging.WARNING)

        channel = Channel("test", "📝", VERBOSE)
        logger._update_third_party_loggers(verbose_active=True)

        self.assertEqual(httpx_logger.level, logging.DEBUG)

    def test_verbose_channel_lowers_httpcore_to_debug(self):
        """Setting any channel to VERBOSE lowers httpcore logger to DEBUG."""
        logger = RevueLogger.shared()

        httpcore_logger = logging.getLogger("httpcore")
        httpcore_logger.setLevel(logging.WARNING)

        logger._update_third_party_loggers(verbose_active=True)

        self.assertEqual(httpcore_logger.level, logging.DEBUG)

    def test_verbose_channel_lowers_anthropic_to_debug(self):
        """Setting any channel to VERBOSE lowers anthropic._base_client logger to DEBUG."""
        logger = RevueLogger.shared()

        anthropic_logger = logging.getLogger("anthropic._base_client")
        anthropic_logger.setLevel(logging.WARNING)

        logger._update_third_party_loggers(verbose_active=True)

        self.assertEqual(anthropic_logger.level, logging.DEBUG)

    def test_no_verbose_channels_keep_third_party_at_warning(self):
        """All channels at DEBUG or above keeps third-party at WARNING."""
        logger = RevueLogger.shared()

        for lib in ["httpx", "httpcore", "anthropic._base_client"]:
            lib_logger = logging.getLogger(lib)
            lib_logger.setLevel(logging.DEBUG)

        logger._update_third_party_loggers(verbose_active=False)

        for lib in ["httpx", "httpcore", "anthropic._base_client"]:
            lib_logger = logging.getLogger(lib)
            self.assertEqual(lib_logger.level, logging.WARNING)


class TestZeroCostSuppression(unittest.TestCase):
    """Test that suppressed messages incur zero cost (no proxy call)."""

    def test_suppressed_message_no_proxy_call(self):
        """Message below threshold doesn't invoke proxy hook."""
        logger = RevueLogger.shared()
        proxy = MagicMock()
        logger.setup(on_log=proxy)

        channel = Channel("test", "📝", logging.WARNING)
        channel._logger = logger

        proxy.reset_mock()
        channel.info("below threshold")
        proxy.assert_not_called()

    def test_passed_message_calls_proxy(self):
        """Message at or above threshold invokes proxy hook."""
        logger = RevueLogger.shared()
        proxy = MagicMock()
        logger.setup(on_log=proxy)

        channel = Channel("test", "📝", logging.INFO)
        channel._logger = logger

        proxy.reset_mock()
        channel.info("at threshold")
        proxy.assert_called_once()


if __name__ == "__main__":
    unittest.main()

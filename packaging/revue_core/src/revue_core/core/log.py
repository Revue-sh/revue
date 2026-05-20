"""RevueLogger — Named-channel logging module with independent level control.

Core classes for semantic logging with emoji markers, call-site metadata,
proxy hooks, file rotation, and VERBOSE-triggered third-party logger control.

Zero-import design: this module has zero imports from src/revue/. It is
fully portable and self-contained.
"""

import inspect
import logging
import os
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

# Register VERBOSE custom log level (numeric 5)
logging.addLevelName(5, "VERBOSE")
VERBOSE = 5


class Channel:
    """Named logging channel with emoji prefix and independent level threshold.

    Each channel has a name (e.g., "pipeline"), emoji marker (e.g., "🔧"),
    and level threshold. Messages below the threshold are suppressed (zero-cost).
    Messages passing the threshold are dispatched through the proxy hook.
    """

    def __init__(self, name: str, emoji: str, level: int):
        """Initialize channel with name, emoji, and default level.

        Args:
            name: Channel name (e.g., "pipeline")
            emoji: Emoji prefix for all log lines (e.g., "🔧")
            level: Initial log level threshold (e.g., logging.INFO)
        """
        self.name = name
        self.emoji = emoji
        self.level = level
        self._logger: Optional["RevueLogger"] = None
        self._env_var = f"REVUE_LOG_{name.upper()}"
        self._resolve_env_var()

    def _resolve_env_var(self) -> None:
        """Check environment variable for level override."""
        env_value = os.environ.get(self._env_var, "").lower()
        if not env_value:
            return

        level_map = {
            "verbose": VERBOSE,
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "off": logging.CRITICAL + 1,
        }
        if env_value in level_map:
            self.level = level_map[env_value]

    def set_level(self, level: int) -> None:
        """Set the channel's log level threshold."""
        self.level = level
        if self._logger:
            self._logger._update_third_party_loggers(self._is_any_verbose())

    def _is_any_verbose(self) -> bool:
        """Check if any registered channel is at VERBOSE level."""
        # Iterate Log._channels — the authoritative registry.
        # RevueLogger._channels is separate and always empty.
        for channel in Log._channels.values():
            if channel.level <= VERBOSE:
                return True
        return False

    def verbose(self, message: str, *args: any, **kwargs: any) -> None:
        """Log at VERBOSE level (numeric 5)."""
        self._log(VERBOSE, message, *args)

    def debug(self, message: str, *args: any, **kwargs: any) -> None:
        """Log at DEBUG level."""
        self._log(logging.DEBUG, message, *args)

    def info(self, message: str, *args: any, **kwargs: any) -> None:
        """Log at INFO level."""
        self._log(logging.INFO, message, *args)

    def warning(self, message: str, *args: any, **kwargs: any) -> None:
        """Log at WARNING level."""
        self._log(logging.WARNING, message, *args)

    def error(self, message: str, *args: any, **kwargs: any) -> None:
        """Log at ERROR level."""
        self._log(logging.ERROR, message, *args)

    def exception(self, message: str, *args: any, **kwargs: any) -> None:
        """Log at ERROR level including the current exception traceback.

        Mirrors logging.Logger.exception — always call from inside an except block.
        """
        tb = traceback.format_exc()
        if tb and tb.strip() not in ("NoneType: None", "None"):
            self._log(logging.ERROR, message + "\n" + tb, *args)
        else:
            self._log(logging.ERROR, message, *args)

    def _log(self, level: int, message: str, *args: any) -> None:
        """Internal log dispatch with threshold check."""
        if level < self.level:
            return  # Suppressed: zero-cost

        # Interpolate format string if args provided
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                message = f"{message} {args}"

        # Capture call-site metadata (skip Channel._log frame to get to actual caller)
        frame = inspect.currentframe()
        if frame and frame.f_back and frame.f_back.f_back:
            # Skip: _log -> verbose/debug/info/warning/error/exception -> actual caller
            caller_frame = frame.f_back.f_back
            filename = os.path.basename(caller_frame.f_code.co_filename)
            function = caller_frame.f_code.co_name
            lineno = caller_frame.f_lineno
        else:
            filename, function, lineno = "<unknown>", "<unknown>", 0

        level_name = logging.getLevelName(level)
        formatted_message = (
            f"{self.emoji} [{self.name}] {level_name} "
            f"({filename}:{function}:{lineno}) {message}"
        )

        # Dispatch through proxy hook
        if self._logger:
            self._logger.dispatch(formatted_message)


class FileLogger:
    """Date-based file logger with 5-day retention and midnight rotation.

    Writes to ~/.config/revue/logs/YYYY-MM-DD.log, rotates at midnight,
    cleans up files older than 5 days. Gracefully handles permission errors.
    """

    def __init__(self, log_dir: str = None):
        """Initialize file logger.

        Args:
            log_dir: Directory for log files. Defaults to ~/.config/revue/logs
        """
        if log_dir is None:
            log_dir = os.path.expanduser("~/.config/revue/logs")

        self.log_dir = log_dir
        self._disabled = False
        self._lock = threading.Lock()
        self._current_file: Optional[str] = None
        self._file_handle: Optional[object] = None
        self._startup_warning: str = ""

        self._ensure_dir_exists()

    def _ensure_dir_exists(self) -> None:
        """Create log directory if missing."""
        try:
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as exc:
            self._disabled = True
            # Store the warning; emit via Log.cli once channels are registered.
            self._startup_warning = (
                f"FileLogger: log directory {self.log_dir!r} is unwritable — "
                f"file logging disabled: {exc}"
            )

    def _get_dated_filename(self) -> str:
        """Get YYYY-MM-DD.log filename for today."""
        today = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{today}.log")

    def _try_emit_startup_warning(self) -> None:
        """Emit a deferred startup warning via Log.cli if the channel is now registered."""
        if not self._startup_warning:
            return
        try:
            Log.cli.warning(self._startup_warning)
            self._startup_warning = ""
        except AttributeError:
            pass  # cli channel not yet registered; will retry next write

    def write(self, message: str) -> None:
        """Write message to dated log file, rotating at midnight."""
        if self._disabled:
            self._try_emit_startup_warning()
            return

        with self._lock:
            try:
                dated_file = self._get_dated_filename()

                # Rotate when date changes; run cleanup only at rotation
                if self._current_file != dated_file:
                    if self._file_handle:
                        self._file_handle.close()
                    self._current_file = dated_file
                    self._file_handle = open(dated_file, "a")
                    self.cleanup()

                self._file_handle.write(message + "\n")
                self._file_handle.flush()

            except (OSError, PermissionError) as exc:
                self._disabled = True
                self._startup_warning = (
                    f"FileLogger: write to {self.log_dir!r} failed — "
                    f"file logging disabled: {exc}"
                )

    def cleanup(self) -> None:
        """Remove log files older than 5 days."""
        if self._disabled:
            return

        try:
            now = datetime.now()
            cutoff = now - timedelta(days=5)

            for filename in os.listdir(self.log_dir):
                filepath = os.path.join(self.log_dir, filename)
                if not filename.endswith(".log"):
                    continue

                # Parse YYYY-MM-DD from filename
                try:
                    file_date = datetime.strptime(filename[:10], "%Y-%m-%d")
                    if file_date < cutoff:
                        os.remove(filepath)
                except (ValueError, OSError):
                    pass

        except (OSError, PermissionError):
            pass


class RevueLogger:
    """Singleton logging engine managing channels, proxy hooks, and file output.

    RevueLogger.shared() is the single entry point. It manages:
    - Channel dispatch (calls from all channels route through here)
    - Proxy hook setup (setup(on_log=...))
    - FileLogger for rotating log files
    - Third-party logger level control when VERBOSE is active
    """

    _instance: Optional["RevueLogger"] = None
    _lock = threading.Lock()

    def __new__(cls):
        """Lazy singleton initialization."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize singleton instance."""
        self._proxy_hook: Optional[Callable[[str], None]] = None
        self._file_logger = FileLogger()
        self._yaml_config: dict[str, str] = {}
        self._yaml_parse_error: str = ""
        self._load_yaml_config()

    @classmethod
    def shared(cls) -> "RevueLogger":
        """Return shared singleton instance."""
        return cls()

    def setup(self, on_log: Callable[[str], None]) -> None:
        """Register proxy hook called for all dispatched messages.

        Args:
            on_log: Callable receiving formatted log messages
        """
        self._proxy_hook = on_log

    def dispatch(self, message: str) -> None:
        """Dispatch formatted message through proxy hook and file logger.

        Args:
            message: Formatted log message with emoji, metadata, etc.
        """
        if self._proxy_hook:
            self._proxy_hook(message)
        self._file_logger.write(message)

    def _load_yaml_config(self) -> None:
        """Load YAML config from ~/.config/revue/log_channels.yaml into self._yaml_config.

        Channels are registered after this runs, so config is stored and applied
        lazily per channel in _apply_yaml_to_channel().
        """
        config_path = self._get_config_path()
        if not os.path.exists(config_path):
            return

        try:
            import yaml
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            # Store raw string values; level parsing happens at apply time
            self._yaml_config = {k: str(v) for k, v in raw.items() if isinstance(k, str)}
        except Exception as exc:
            self._yaml_parse_error = str(exc)

    def _apply_yaml_to_channel(self, channel: "Channel") -> None:
        """Apply stored YAML config to a newly registered channel.

        Env var takes precedence: if REVUE_LOG_<CHANNEL> is set, YAML is skipped
        for that channel so the priority chain env var > YAML > default is honoured.
        """
        level_map = {
            "verbose": VERBOSE,
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
        }
        level_str = self._yaml_config.get(channel.name, "").lower()
        if not level_str:
            return
        # Skip if env var already overrode the level
        if os.environ.get(channel._env_var, "").lower():
            return
        if level_str in level_map:
            channel.set_level(level_map[level_str])

    @staticmethod
    def _get_config_path() -> str:
        """Get path to log_channels.yaml config file."""
        return os.path.expanduser("~/.config/revue/log_channels.yaml")

    def _update_third_party_loggers(self, verbose_active: bool) -> None:
        """Update third-party logger levels based on VERBOSE activation."""
        third_party = ["httpx", "httpcore", "anthropic._base_client"]
        target_level = logging.DEBUG if verbose_active else logging.WARNING

        for lib_name in third_party:
            logger = logging.getLogger(lib_name)
            logger.setLevel(target_level)


class _LogMeta(type):
    """Metaclass enabling dynamic attribute access to registered channels."""

    def __getattr__(cls, name: str) -> Channel:
        """Dynamic access to registered channels."""
        if name in cls._channels:
            return cls._channels[name]
        raise AttributeError(f"No logging channel named '{name}'. Available: {list(cls._channels.keys())}")


class Log(metaclass=_LogMeta):
    """Namespace for registered logging channels.

    Use Log.register() to create channels at startup. Access via dynamic
    attributes: Log.pipeline, Log.agent, Log.nova, Log.cli.
    """

    _channels: dict[str, Channel] = {}

    @classmethod
    def register(cls, name: str, emoji: str, level: int) -> Channel:
        """Register a new logging channel.

        Args:
            name: Channel name (e.g., "pipeline")
            emoji: Emoji prefix (e.g., "🔧")
            level: Default log level (e.g., logging.INFO)

        Returns:
            The created Channel instance
        """
        channel = Channel(name, emoji, level)
        channel._logger = RevueLogger.shared()
        cls._channels[name] = channel

        # Apply YAML config at registration time (env var already applied in constructor)
        RevueLogger.shared()._apply_yaml_to_channel(channel)

        # Once the cli channel is registered, surface any deferred warnings
        if name == "cli":
            yaml_err = RevueLogger.shared()._yaml_parse_error
            if yaml_err:
                channel.warning(
                    "log_channels.yaml is malformed — falling back to code defaults: %s",
                    yaml_err,
                )
                RevueLogger.shared()._yaml_parse_error = ""
            RevueLogger.shared()._file_logger._try_emit_startup_warning()

        return channel

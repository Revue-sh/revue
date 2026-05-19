"""ClaudeCodeClient — AIClient implementation backed by the 'claude' CLI.

Satisfies the revue.core.ai_client.AIClient protocol without importing the
Anthropic or OpenAI SDK. All AI calls go through 'claude --print --bare',
which uses the current Claude Code session credentials.

Usage in debug tooling only. Never import this from src/revue/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

# Make production code importable when called via scripts/local_run.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from revue.core.ai_client import CompletionResult, TokenUsage


def _flatten_system(system: "str | list[dict[str, Any]] | None") -> str:
    """Flatten a system prompt (string or Anthropic content-block list) to plain text."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(
        block.get("text", "")
        for block in system
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Extract the last user message text from a messages list."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    return ""


class ClaudeCodeClient:
    """Routes AI calls through 'claude --print --bare' instead of the Anthropic SDK.

    --bare   skips CLAUDE.md, hooks, memory — a clean API-like call.
    --tools "" disables all Claude Code tools so output is pure text.
    """

    def __init__(self, timeout_seconds: int = 120, model: str = "haiku") -> None:
        self._timeout = timeout_seconds
        self._model = model

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        system: "str | list[dict[str, Any]] | None" = None,
        cache_key: "str | None" = None,
        agent_name: "str | None" = None,
    ) -> CompletionResult:
        sys_text = _flatten_system(system)
        user_text = _flatten_messages(messages)

        # System prompt passed as flag (kept small — agent persona only).
        # User message piped via stdin to avoid OS ARG_MAX limits on large diffs.
        cmd = ["claude", "--print", "--bare", "--tools", "", "--model", self._model]
        if sys_text:
            cmd += ["--system-prompt", sys_text]

        result = subprocess.run(
            cmd, input=user_text,
            capture_output=True, text=True,
            timeout=self._timeout, cwd=str(_REPO_ROOT),
        )
        if result.returncode != 0:
            label = f"agent={agent_name}" if agent_name else "ClaudeCodeClient"
            raise RuntimeError(
                f"claude --print failed ({label}):\n{result.stderr[:400]}"
            )
        return CompletionResult(text=result.stdout, usage=TokenUsage())

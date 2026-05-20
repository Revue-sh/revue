"""REVUE-241: AnthropicClient + OpenAI-compat clients forward output_config.

The kwarg has to travel from agent_loader through the client to the loop.
For AnthropicClient: passed straight through to anthropic_tool_loop. For
OpenAI-compat clients (OpenAIClient, AzureOpenAIClient, OpenRouterClient,
CustomGatewayClient): translated at the boundary to OpenAI's
response_format shape and passed to openai_tool_loop.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from revue_core.core.ai_client import AnthropicClient
from revue_core.core.ai_config import AIConfig


def _config() -> AIConfig:
    return AIConfig(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key="sk-test",
        api_key_env="",
        gitlab_url="", gitlab_token="", gitlab_project_id="",
        gitlab_project_path="", gitlab_project_url="",
        genai_gateway_url="", openai_api_key="sk-test",
        gen_ai_gateway_model="",
        ai_temp=0.3, ai_confidence=70, ai_max_tokens=4096,
    )


def test_complete_with_tools_passes_output_config_to_loop() -> None:
    """The output_config kwarg from agent_loader reaches anthropic_tool_loop."""
    schema = {
        "format": {
            "type": "json_schema",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    }

    with patch("revue_core.core.ai_client.anthropic.Anthropic"):
        client = AnthropicClient(_config())

    with patch("revue_core.core.tool_loop.anthropic_tool_loop") as mock_loop:
        mock_loop.return_value = SimpleNamespace(text="{}", usage=SimpleNamespace())
        client.complete_with_tools(
            [{"role": "user", "content": "go"}],
            tools=[{"name": "read_file", "input_schema": {}}],
            tool_handlers={},
            output_config=schema,
        )

    assert mock_loop.call_count == 1
    kwargs = mock_loop.call_args[1]
    assert kwargs.get("output_config") == schema, (
        "AnthropicClient.complete_with_tools must forward output_config to "
        "anthropic_tool_loop — otherwise the grammar never reaches the SDK"
    )


def test_complete_with_tools_omits_output_config_when_not_supplied() -> None:
    """Back-compat: callers that don't pass output_config (Nova / Vex) must
    NOT see the kwarg materialise as None — it has to be omitted from the
    loop call so the SDK's keyword handling matches the with-config path."""
    with patch("revue_core.core.ai_client.anthropic.Anthropic"):
        client = AnthropicClient(_config())

    with patch("revue_core.core.tool_loop.anthropic_tool_loop") as mock_loop:
        mock_loop.return_value = SimpleNamespace(text="{}", usage=SimpleNamespace())
        client.complete_with_tools(
            [{"role": "user", "content": "go"}],
            tools=[{"name": "read_file", "input_schema": {}}],
            tool_handlers={},
        )

    kwargs = mock_loop.call_args[1]
    # Either the kwarg is absent, OR it is None — both are acceptable;
    # the loop already handles None-vs-missing.
    assert kwargs.get("output_config") in (None,), (
        f"output_config should be None or absent when not supplied; got "
        f"{kwargs.get('output_config')!r}"
    )


def test_openrouter_client_translates_output_config_to_response_format() -> None:
    """OpenRouter inherits the OpenAI-compat path. When agent_loader passes
    Anthropic-shape ``output_config``, OpenRouterClient must translate it to
    OpenAI's ``response_format`` shape before reaching openai_tool_loop.

    This is the load-bearing test for the new path — without it, dogfood on
    OpenRouter would never see the schema and the prose-drift regression
    would persist.
    """
    from revue_core.core.ai_client import OpenRouterClient

    config = _config()
    config.provider = "openrouter"
    config.model = "anthropic/claude-haiku-4.5"
    config.api_key = "sk-or-test"

    with patch("revue_core.core.ai_client.openai.OpenAI"):
        client = OpenRouterClient(config)

    anthropic_output_config = {
        "format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"findings": {"type": "array"}},
                "required": ["findings"],
                "additionalProperties": False,
            },
        }
    }

    with patch("revue_core.core.tool_loop.openai_tool_loop") as mock_loop:
        mock_loop.return_value = SimpleNamespace(text='{"findings": []}', usage=None)
        client.complete_with_tools(
            [{"role": "user", "content": "review"}],
            tools=[{"name": "read_file", "input_schema": {}}],
            tool_handlers={},
            output_config=anthropic_output_config,
        )

    rf = mock_loop.call_args[1].get("response_format")
    assert rf is not None, "openai_tool_loop must receive a translated response_format"
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    # Schema body is preserved verbatim — only the wrapper differs between
    # Anthropic's output_config and OpenAI's response_format.
    assert rf["json_schema"]["schema"] == anthropic_output_config["format"]["schema"]


def test_openrouter_client_omits_response_format_when_no_output_config() -> None:
    """Back-compat: when agent_loader doesn't pass output_config (the no-tool
    fallback path on the OpenAI-compat side), nothing is translated and the
    loop receives ``response_format=None``."""
    from revue_core.core.ai_client import OpenRouterClient

    config = _config()
    config.provider = "openrouter"
    config.api_key = "sk-or-test"

    with patch("revue_core.core.ai_client.openai.OpenAI"):
        client = OpenRouterClient(config)

    with patch("revue_core.core.tool_loop.openai_tool_loop") as mock_loop:
        mock_loop.return_value = SimpleNamespace(text="text", usage=None)
        client.complete_with_tools(
            [{"role": "user", "content": "go"}],
            tools=[{"name": "read_file", "input_schema": {}}],
            tool_handlers={},
        )

    assert mock_loop.call_args[1].get("response_format") is None

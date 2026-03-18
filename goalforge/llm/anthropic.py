"""Claude API provider via the official anthropic SDK."""
import json
import logging

from goalforge.llm.base import LLMProvider, LLMError, ToolCall

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    def __init__(self, cfg):
        try:
            import anthropic as sdk
            self._client = sdk.Anthropic(api_key=cfg.api_key)
            self._model = cfg.model or "claude-sonnet-4-6"
        except ImportError:
            raise LLMError("anthropic package not installed. Run: pip install anthropic")

    def chat(self, system: str, messages: list[dict], json_mode: bool = False) -> str:
        try:
            if json_mode:
                system = system + "\n\nRespond with valid JSON only. No markdown, no explanation."
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=messages,
            )
            return response.content[0].text
        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            raise LLMError(f"Anthropic error: {e}") from e

    def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[str, list[ToolCall]]:
        # Convert OpenAI-style tool schema to Anthropic format
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", {})
            anthropic_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=anthropic_tools,
            )
        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            raise LLMError(f"Anthropic error: {e}") from e

        content_text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))
        return content_text, tool_calls

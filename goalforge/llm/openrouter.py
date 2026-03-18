"""OpenRouter API provider (OpenAI-compatible REST API)."""
import json
import logging

import httpx

from goalforge.llm.base import LLMProvider, LLMError, ToolCall

logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(LLMProvider):
    def __init__(self, cfg):
        self._api_key = cfg.api_key
        self._model = cfg.model or "mistralai/mistral-7b-instruct"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, body: dict) -> dict:
        try:
            response = httpx.post(_BASE_URL, headers=self._headers(), json=body, timeout=120)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("OpenRouter HTTP error %s: %s", e.response.status_code, e.response.text)
            raise LLMError(f"OpenRouter HTTP {e.response.status_code}: {e.response.text}") from e
        except Exception as e:
            logger.error("OpenRouter error: %s", e)
            raise LLMError(f"OpenRouter error: {e}") from e

    def chat(self, system: str, messages: list[dict], json_mode: bool = False) -> str:
        body: dict = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}] + messages,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(body)
        return data["choices"][0]["message"]["content"]

    def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[str, list[ToolCall]]:
        body: dict = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}] + messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        data = self._post(body)
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=tc["function"]["name"],
                arguments=args,
            ))
        return content, tool_calls

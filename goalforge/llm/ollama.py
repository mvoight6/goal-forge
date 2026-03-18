"""Ollama local LLM provider."""
import json
import logging

import httpx

from goalforge.llm.base import LLMProvider, LLMError, ToolCall

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    def __init__(self, cfg):
        self._base_url = (cfg.base_url or "http://localhost:11434").rstrip("/")
        self._model = cfg.model or "llama3"

    def _post(self, body: dict) -> dict:
        try:
            response = httpx.post(
                f"{self._base_url}/api/chat",
                json=body,
                timeout=300,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("Ollama HTTP error %s: %s", e.response.status_code, e.response.text)
            raise LLMError(f"Ollama HTTP {e.response.status_code}: {e.response.text}") from e
        except Exception as e:
            logger.error("Ollama error: %s", e)
            raise LLMError(f"Ollama error: {e}") from e

    def chat(self, system: str, messages: list[dict], json_mode: bool = False) -> str:
        body: dict = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
        }
        if json_mode:
            body["format"] = "json"
        data = self._post(body)
        return data["message"]["content"]

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
            "stream": False,
        }
        data = self._post(body)
        message = data["message"]
        content = message.get("content") or ""
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=args,
            ))
        return content, tool_calls

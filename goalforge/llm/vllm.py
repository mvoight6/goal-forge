"""vLLM provider — uses OpenAI-compatible /v1/chat/completions endpoint."""
import json
import logging
import re

import httpx

from goalforge.llm.base import LLMProvider, LLMError, ToolCall

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_TOOL_CALL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _parse_xml_tool_calls(content: str) -> list[ToolCall]:
    """
    Parse the XML tool-call format some models emit:
      <tool_call>
      <function=name>
      <parameter=key>value</parameter>
      </function>
      </tool_call>
    """
    tool_calls = []
    for block in re.finditer(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
        inner = block.group(1).strip()
        fn_match = re.match(r"<function=(\S+?)>(.*?)</function>", inner, re.DOTALL)
        if not fn_match:
            # Try alternate: <function=name/> with params outside
            fn_match = re.match(r"<function=(\S+?)>(.*)", inner, re.DOTALL)
        if not fn_match:
            continue
        fn_name = fn_match.group(1).strip(">/ ")
        fn_body = fn_match.group(2)
        params: dict = {}
        for pm in re.finditer(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", fn_body, re.DOTALL):
            key = pm.group(1)
            raw = pm.group(2).strip()
            # Coerce types
            if raw.lower() == "true":
                params[key] = True
            elif raw.lower() == "false":
                params[key] = False
            else:
                try:
                    params[key] = int(raw)
                except ValueError:
                    try:
                        params[key] = float(raw)
                    except ValueError:
                        params[key] = raw
        tool_calls.append(ToolCall(id=f"xml_{fn_name}_{len(tool_calls)}", name=fn_name, arguments=params))
    return tool_calls


class VLLMProvider(LLMProvider):
    def __init__(self, cfg):
        base = (cfg.base_url or "http://localhost:8000/v1").rstrip("/")
        self._endpoint = f"{base}/chat/completions"
        self._model = cfg.model or "mistralai/Mistral-7B-Instruct-v0.2"
        self._api_key = cfg.api_key or "local"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    def _post(self, body: dict) -> dict:
        try:
            response = httpx.post(
                self._endpoint,
                headers=self._headers(),
                json=body,
                timeout=300,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("vLLM HTTP error %s: %s", e.response.status_code, e.response.text)
            raise LLMError(f"vLLM HTTP {e.response.status_code}: {e.response.text}") from e
        except Exception as e:
            logger.error("vLLM error: %s", e)
            raise LLMError(f"vLLM error: {e}") from e

    def chat(self, system: str, messages: list[dict], json_mode: bool = False) -> str:
        body: dict = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}] + messages,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(body)
        return _strip_thinking(data["choices"][0]["message"]["content"])

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
            "chat_template_kwargs": {"enable_thinking": False},
        }
        data = self._post(body)
        message = data["choices"][0]["message"]
        raw_content = message.get("content") or ""
        tool_calls = []

        # Try native tool_calls first
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

        # Fall back to parsing XML tool-call tags the model emitted as text
        if not tool_calls and "<tool_call>" in raw_content:
            tool_calls = _parse_xml_tool_calls(raw_content)
            raw_content = _TOOL_CALL_RE.sub("", raw_content).strip()

        content = _strip_thinking(raw_content)
        return content, tool_calls

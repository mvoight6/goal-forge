"""Abstract LLM provider base class."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(Exception):
    """Raised by any provider on unrecoverable errors."""
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, system: str, messages: list[dict], json_mode: bool = False) -> str:
        """
        Send a plain chat request. Returns the assistant's reply as a string.
        Raises LLMError on failure.
        """
        pass

    def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[str, list[ToolCall]]:
        """
        Send a chat request with native tool/function calling.
        Returns (text_reply, list_of_tool_calls).
        Default implementation falls back to plain chat (no tool use).
        Override in providers that support native tool calling.
        """
        return self.chat(system, messages), []

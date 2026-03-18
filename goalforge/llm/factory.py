"""
LLM provider factory. The single place in the codebase that reads config.llm.provider
and returns the correct LLMProvider instance.
"""
import logging

from goalforge.config import config
from goalforge.llm.base import LLMProvider, LLMError

logger = logging.getLogger(__name__)

_provider_cache: dict[str, LLMProvider] = {}


def get_provider(force_reload: bool = False) -> LLMProvider:
    """
    Return the active LLM provider based on current config.
    Caches the instance per provider name; pass force_reload=True after config change.
    """
    provider_name = config.llm.provider
    if not provider_name:
        raise LLMError("config.yaml: llm.provider is not set")

    if not force_reload and provider_name in _provider_cache:
        return _provider_cache[provider_name]

    match provider_name:
        case "anthropic":
            from goalforge.llm.anthropic import AnthropicProvider
            instance = AnthropicProvider(config.llm.anthropic)
        case "openrouter":
            from goalforge.llm.openrouter import OpenRouterProvider
            instance = OpenRouterProvider(config.llm.openrouter)
        case "ollama":
            from goalforge.llm.ollama import OllamaProvider
            instance = OllamaProvider(config.llm.ollama)
        case "vllm":
            from goalforge.llm.vllm import VLLMProvider
            instance = VLLMProvider(config.llm.vllm)
        case _:
            raise LLMError(f"Unknown LLM provider: '{provider_name}'. Choose: anthropic | openrouter | ollama | vllm")

    _provider_cache[provider_name] = instance
    logger.info("LLM provider loaded: %s", provider_name)
    return instance


def clear_cache():
    """Call after config reload so the next get_provider() re-instantiates."""
    _provider_cache.clear()

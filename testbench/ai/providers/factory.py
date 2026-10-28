"""Construct a configured provider without importing optional SDKs."""

from typing import Any

from testbench.ai.providers.anthropic import AnthropicProvider
from testbench.ai.providers.base import LLMProvider
from testbench.ai.providers.gemini import GeminiProvider
from testbench.ai.providers.ollama import OllamaProvider
from testbench.ai.providers.openai_compatible import OpenAICompatibleProvider


def create_provider(config: dict[str, Any]) -> LLMProvider:
    """Build the selected provider from a normalized AI configuration."""
    name = str(config.get("provider", "ollama")).lower()
    options: dict[str, Any] = {
        "timeout": float(config.get("timeout", 30)),
        "retries": int(config.get("retries", 2)),
    }
    if name == "ollama":
        return OllamaProvider(config.get("base_url") or "http://localhost:11434", **options)
    if name == "gemini":
        return GeminiProvider(**options)
    if name in {"openai_compatible", "openai", "groq", "openrouter", "lm_studio"}:
        defaults = {
            "openai": "https://api.openai.com/v1",
            "groq": "https://api.groq.com/openai/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "lm_studio": "http://localhost:1234/v1",
        }
        return OpenAICompatibleProvider(
            config.get("base_url") or defaults.get(name, "https://api.openai.com/v1"),
            api_key_env=str(config.get("api_key_env", "OPENAI_API_KEY")),
            **options,
        )
    if name == "anthropic":
        return AnthropicProvider(**options)
    raise ValueError(f"Unsupported AI provider {name!r}. Choose ollama, gemini, openai_compatible, or anthropic.")

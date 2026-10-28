"""Anthropic Messages API adapter."""

import os
from typing import Any

from testbench.ai.providers.base import LLMProvider, ProviderError


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key_env: str = "ANTHROPIC_API_KEY", **options: Any) -> None:
        super().__init__(**options)
        self.api_key_env = api_key_env

    def generate(self, prompt, system, images=None, json_schema=None, *, model=None) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ProviderError(f"Set {self.api_key_env} to use Anthropic.")
        if not model:
            raise ValueError("An Anthropic model name is required.")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images or []:
            import base64

            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                }
            )
        result = self._request_json(
            "https://api.anthropic.com/v1/messages",
            {"model": model, "max_tokens": 2048, "system": system, "messages": [{"role": "user", "content": content}]},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        try:
            text = "".join(part["text"] for part in result["content"] if part.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise ProviderError("Anthropic returned an unexpected response shape.") from exc
        if not text:
            raise ProviderError("Anthropic returned no text.")
        return text

    def health(self, model: str) -> tuple[bool, str]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            return False, f"Set {self.api_key_env} to use Anthropic."
        try:
            result = self._request_json(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                method="GET",
            )
        except ProviderError as exc:
            return False, f"Anthropic is unavailable: {exc}."
        names = [item.get("id", "") for item in result.get("data", [])]
        if model in names:
            return True, f"Anthropic is reachable and model {model!r} is available."
        return False, f"Model {model!r} was not listed by Anthropic."

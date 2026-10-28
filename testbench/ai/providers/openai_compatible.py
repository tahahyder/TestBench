"""OpenAI Chat Completions-compatible provider adapter."""

import base64
import os
from typing import Any

from testbench.ai.providers.base import LLMProvider, ProviderError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        **options: Any,
    ) -> None:
        super().__init__(**options)
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env

    def generate(self, prompt, system, images=None, json_schema=None, *, model=None) -> str:
        if not model:
            raise ValueError("A model name is required for the OpenAI-compatible provider.")
        key = os.environ.get(self.api_key_env, "")
        if not key and self.base_url.startswith("https://api.openai.com"):
            raise ProviderError(f"Set {self.api_key_env} to use this provider.")
        content: Any = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")},
                }
                for image in images
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0,
        }
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema, "strict": True},
            }
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        result = self._request_json(f"{self.base_url}/chat/completions", payload, headers=headers)
        try:
            text = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI-compatible provider returned an unexpected response shape.") from exc
        if not isinstance(text, str):
            raise ProviderError("OpenAI-compatible provider returned no text.")
        return text

    def health(self, model: str) -> tuple[bool, str]:
        key = os.environ.get(self.api_key_env, "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            result = self._request_json(f"{self.base_url}/models", headers=headers, method="GET")
        except ProviderError as exc:
            return False, f"Provider is unreachable: {exc}."
        names = [item.get("id", "") for item in result.get("data", [])]
        if not names or model in names:
            return (
                True,
                f"Provider is reachable{'; model could not be enumerated' if not names else f' and model {model!r} exists'}.",
            )
        return False, f"Model {model!r} was not listed by the endpoint."

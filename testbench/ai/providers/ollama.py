"""Ollama local HTTP provider."""

import base64
from typing import Any

from testbench.ai.providers.base import LLMProvider, ProviderError


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str = "http://localhost:11434", **options: Any) -> None:
        super().__init__(**options)
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt, system, images=None, json_schema=None, *, model=None) -> str:
        if not model:
            raise ValueError("An Ollama model name is required.")
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            message["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        options: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, message],
            "stream": False,
        }
        if json_schema:
            options["format"] = json_schema
        result = self._request_json(f"{self.base_url}/api/chat", options)
        try:
            content = result["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Ollama returned an unexpected response shape.") from exc
        if not isinstance(content, str):
            raise ProviderError("Ollama returned no text.")
        return content

    def health(self, model: str) -> tuple[bool, str]:
        try:
            response = self._request_json(f"{self.base_url}/api/tags", method="GET")
            names = [item.get("name", "") for item in response.get("models", [])]
        except (ProviderError, AttributeError, TypeError) as exc:
            return False, f"Ollama is unavailable: {exc}. Start it or run `ollama serve`."
        if any(name == model or name.startswith(model + ":") for name in names):
            return True, f"Ollama is reachable and model {model!r} is installed."
        return False, f"Model {model!r} is missing; install it with `ollama pull {model}`."

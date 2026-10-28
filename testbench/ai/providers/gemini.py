"""Google Gemini generateContent API adapter."""

import base64
import os
from typing import Any
from urllib.parse import quote

from testbench.ai.providers.base import LLMProvider, ProviderError


class GeminiProvider(LLMProvider):
    def __init__(self, api_key_env: str = "GEMINI_API_KEY", **options: Any) -> None:
        super().__init__(**options)
        self.api_key_env = api_key_env

    def generate(self, prompt, system, images=None, json_schema=None, *, model=None) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ProviderError(f"Set {self.api_key_env} to use Gemini.")
        if not model:
            raise ValueError("A Gemini model name is required.")
        parts: list[dict[str, Any]] = [{"text": f"{system}\n\n{prompt}"}]
        for image in images or []:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode("ascii")}})
        generation: dict[str, Any] = {"temperature": 0}
        if json_schema:
            generation.update({"responseMimeType": "application/json", "responseSchema": json_schema})
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent?key={quote(key, safe='')}"
        result = self._request_json(url, {"contents": [{"parts": parts}], "generationConfig": generation})
        try:
            text = result["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Gemini returned an unexpected response shape.") from exc
        if not isinstance(text, str):
            raise ProviderError("Gemini returned no text.")
        return text

    def health(self, model: str) -> tuple[bool, str]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            return False, f"Set {self.api_key_env} to enable Gemini."
        try:
            result = self._request_json(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={quote(key, safe='')}",
                method="GET",
            )
        except ProviderError as exc:
            return False, f"Gemini is unavailable: {exc}."
        names = [item.get("name", "").removeprefix("models/") for item in result.get("models", [])]
        if model in names:
            return True, f"Gemini is reachable and model {model!r} is available."
        return False, f"Model {model!r} was not listed; choose an available Gemini model."

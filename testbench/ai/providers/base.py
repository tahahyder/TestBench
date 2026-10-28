"""Shared provider contract and retrying JSON HTTP transport."""

import json
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """A safe, user-facing provider failure without request credentials."""


class LLMProvider(ABC):
    """Generate text through a local or remote model provider."""

    def __init__(self, *, timeout: float = 30, retries: int = 2) -> None:
        if timeout <= 0 or retries < 0:
            raise ValueError("Provider timeout must be positive and retries non-negative.")
        self.timeout = timeout
        self.retries = retries

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str,
        images: list[bytes] | None = None,
        json_schema: dict[str, Any] | None = None,
        *,
        model: str | None = None,
    ) -> str:
        """Return model text for a prompt."""

    @abstractmethod
    def health(self, model: str) -> tuple[bool, str]:
        """Check reachability and whether the requested model is available."""

    def _request_json(
        self,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        method: str = "POST",
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", **(headers or {})},
            method=method,
        )
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ProviderError("Provider returned malformed JSON.")
                return decoded
            except HTTPError as exc:
                if exc.code == 429 or exc.code >= 500:
                    if attempt < self.retries:
                        time.sleep(min(0.25 * (2**attempt), 2))
                        continue
                label = "rate limited" if exc.code == 429 else f"HTTP {exc.code}"
                raise ProviderError(f"AI provider request failed ({label}).") from exc
            except (TimeoutError, URLError) as exc:
                if attempt < self.retries:
                    time.sleep(min(0.25 * (2**attempt), 2))
                    continue
                if isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError):
                    label = "timed out"
                elif isinstance(exc, TimeoutError):
                    label = "timed out"
                else:
                    label = "is unreachable"
                raise ProviderError(f"AI provider {label}; check its URL and availability.") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderError("AI provider returned malformed JSON.") from exc
        raise ProviderError("AI provider request failed after retries.")

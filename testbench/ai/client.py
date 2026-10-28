"""Privacy-aware provider calls, request caching, and usage accounting."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from testbench.ai.config import load_ai_config
from testbench.ai.providers import ProviderError, create_provider
from testbench.ai.redaction import redact_image_bytes, redact_text


class AICallError(RuntimeError):
    """An AI call was disabled, over budget, or failed safely."""


class AIClient:
    """Apply opt-in, redaction, consent, cache, limits, and logging to calls."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        database_path: Path = Path("runs/testbench.sqlite3"),
        provider=None,
    ) -> None:
        self.config = config or load_ai_config()
        self.database_path = database_path
        self.provider = provider or create_provider(self.config)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def generate(
        self,
        prompt: str,
        system: str,
        *,
        images: list[bytes] | None = None,
        json_schema: dict[str, Any] | None = None,
        purpose: str = "general",
        run_id: str = "default",
    ) -> str:
        if not self.config.get("enabled", False):
            raise AICallError("AI is disabled. Set [ai].enabled = true in testbench.toml.")
        provider_name = str(self.config.get("provider", "ollama"))
        if provider_name not in set(self.config.get("consented_providers", [])):
            raise AICallError(
                f"Consent required: page content and screenshots will be sent to {provider_name}. "
                "Use ollama for fully local processing. Record consent in testbench.toml."
            )
        prompt = str(prompt)
        system = str(system)
        redact_count = 0
        prompt, prompt_count = redact_text(prompt)
        system, system_count = redact_text(system)
        redact_count = prompt_count + system_count
        safe_images = []
        for image in images or []:
            safe_image, image_count = redact_image_bytes(image)
            safe_images.append(safe_image)
            redact_count += image_count
        maximum = int(self.config.get("max_prompt_chars", 24000))
        if len(prompt) + len(system) > maximum:
            raise AICallError(f"AI prompt exceeds the configured {maximum}-character limit.")
        key_data = {
            "provider": provider_name,
            "model": self.config.get("model"),
            "system": system,
            "prompt": prompt,
            "schema": json_schema,
            "images": [hashlib.sha256(image).hexdigest() for image in images or []],
        }
        cache_key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        with sqlite3.connect(self.database_path) as connection:
            cached = connection.execute("SELECT response FROM ai_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            if cached:
                self._log_call(connection, run_id, purpose, provider_name, True, 0, redact_count, prompt)
                return cached[0]
            count = connection.execute("SELECT COUNT(*) FROM ai_calls WHERE run_id = ?", (run_id,)).fetchone()[0]
            limit = int(self.config.get("max_calls_per_run", 20))
            if count >= limit:
                raise AICallError(f"AI call limit ({limit}) reached for this run.")
            try:
                response = self.provider.generate(
                    prompt,
                    system,
                    images=safe_images or None,
                    json_schema=json_schema,
                    model=str(self.config.get("model", "")),
                )
            except (ProviderError, OSError, RuntimeError, ValueError):
                self._log_call(
                    connection,
                    run_id,
                    purpose,
                    provider_name,
                    False,
                    _estimate_tokens(system + prompt),
                    redact_count,
                    prompt,
                )
                connection.commit()
                raise
            if not isinstance(response, str) or not response.strip():
                raise AICallError("AI provider returned an empty response.")
            connection.execute(
                "INSERT OR REPLACE INTO ai_cache(cache_key, response) VALUES (?, ?)",
                (cache_key, response),
            )
            self._log_call(
                connection,
                run_id,
                purpose,
                provider_name,
                False,
                _estimate_tokens(system + prompt + response),
                redact_count,
                prompt,
            )
            connection.commit()
        return response

    def usage(self, run_id: str) -> dict[str, int]:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(estimated_tokens), 0), "
                "COALESCE(SUM(cache_hit), 0) FROM ai_calls WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return {"calls": int(row[0]), "estimated_tokens": int(row[1]), "cache_hits": int(row[2])}

    def _initialize(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS ai_cache (cache_key TEXT PRIMARY KEY, response TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS ai_calls ("
                "id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, purpose TEXT NOT NULL, "
                "provider TEXT NOT NULL, model TEXT NOT NULL, estimated_tokens INTEGER NOT NULL, "
                "cache_hit INTEGER NOT NULL, redaction_count INTEGER NOT NULL, prompt TEXT)"
            )

    def _log_call(self, connection, run_id, purpose, provider, cache_hit, tokens, redactions, prompt):
        connection.execute(
            "INSERT INTO ai_calls(run_id, purpose, provider, model, estimated_tokens, "
            "cache_hit, redaction_count, prompt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                purpose,
                provider,
                str(self.config.get("model", "")),
                tokens,
                int(cache_hit),
                redactions,
                prompt if self.config.get("debug_log", False) else None,
            ),
        )


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)

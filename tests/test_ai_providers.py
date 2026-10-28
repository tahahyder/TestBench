import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

from testbench.ai.client import AICallError, AIClient
from testbench.ai.config import load_ai_config
from testbench.ai.providers.base import LLMProvider, ProviderError
from testbench.ai.providers.gemini import GeminiProvider
from testbench.ai.providers.ollama import OllamaProvider
from testbench.ai.providers.openai_compatible import OpenAICompatibleProvider
from testbench.ai.redaction import redact_data, redact_text


class FakeProvider(LLMProvider):
    def __init__(self, response="ok"):
        super().__init__(timeout=1, retries=0)
        self.response = response
        self.calls = []

    def generate(self, prompt, system, images=None, json_schema=None, *, model=None):
        self.calls.append((prompt, system, images, model))
        return self.response

    def health(self, model):
        return True, "ready"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("email qa.person@example.test", "email [REDACTED]"),
        ("token sk-abcdefghijklmnop", "token [REDACTED]"),
        ("card 4111 1111 1111 1111", "card [REDACTED]"),
        ("Authorization: Bearer abcdef123456", "[REDACTED]"),
    ],
)
def test_redacts_sensitive_text(source, expected):
    redacted, count = redact_text(source)
    assert redacted == expected
    assert count == 1


def test_recursively_redacts_password_fields_and_free_text():
    value, count = redact_data({"password": "do-not-send", "visible": "qa@example.test"})
    assert value == {"password": "[REDACTED]", "visible": "[REDACTED]"}
    assert count == 2


def test_ai_client_defaults_disabled_and_requires_consent(tmp_path: Path):
    provider = FakeProvider()
    client = AIClient({"enabled": False, "provider": "ollama"}, database_path=tmp_path / "ai.sqlite", provider=provider)
    with pytest.raises(AICallError, match="disabled"):
        client.generate("hello", "system")
    client.config.update({"enabled": True})
    with pytest.raises(AICallError, match="Consent required"):
        client.generate("hello", "system")
    assert not provider.calls


def test_ai_client_redacts_caches_and_obeys_call_limit(tmp_path: Path):
    provider = FakeProvider("generated")
    config = {
        "enabled": True,
        "provider": "ollama",
        "model": "test",
        "consented_providers": ["ollama"],
        "max_calls_per_run": 1,
        "redact_secrets": True,
    }
    client = AIClient(config, database_path=tmp_path / "ai.sqlite", provider=provider)
    result = client.generate("Use qa@example.test", "safe", purpose="test", run_id="run-1")
    assert result == "generated"
    assert "[REDACTED]" in provider.calls[0][0]
    assert client.generate("Use qa@example.test", "safe", purpose="test", run_id="run-1") == "generated"
    assert len(provider.calls) == 1
    assert client.usage("run-1") == {"calls": 2, "estimated_tokens": 7, "cache_hits": 1}
    with pytest.raises(AICallError, match="limit"):
        client.generate("different", "safe", run_id="run-1")


def test_ai_client_prompt_size_is_enforced(tmp_path: Path):
    client = AIClient(
        {"enabled": True, "provider": "ollama", "consented_providers": ["ollama"], "max_prompt_chars": 4},
        database_path=tmp_path / "ai.sqlite",
        provider=FakeProvider(),
    )
    with pytest.raises(AICallError, match="character limit"):
        client.generate("long prompt", "system")


def test_load_config_merges_safe_defaults(tmp_path: Path):
    path = tmp_path / "testbench.toml"
    path.write_text("[ai]\nenabled = true\nprovider = 'ollama'\n", encoding="utf-8")
    config = load_ai_config(path)
    assert config["enabled"] is True
    assert config["model"] == "llama3.1"
    assert config["redact_secrets"] is True


def _fake_response(data):
    encoded = json.dumps(data).encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return encoded

    return Response()


def test_ollama_provider_formats_request_and_parses_reply(monkeypatch):
    seen = {}

    def request(req, timeout=None):
        seen.update(url=req.full_url, data=json.loads(req.data), headers=req.headers)
        return _fake_response({"message": {"content": "steps"}})

    monkeypatch.setattr("testbench.ai.providers.base.urlopen", request)
    provider = OllamaProvider(retries=0)
    assert provider.generate("prompt", "system", model="llama3.1") == "steps"
    assert seen["url"].endswith("/api/chat")
    assert seen["data"]["model"] == "llama3.1"


def test_gemini_provider_uses_environment_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    seen = {}

    def request(req, timeout=None):
        seen["url"] = req.full_url
        return _fake_response({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    monkeypatch.setattr("testbench.ai.providers.base.urlopen", request)
    provider = GeminiProvider(retries=0)
    assert provider.generate("prompt", "system", model="gemini-test") == "{}"
    assert "secret-key" in seen["url"]


def test_openai_compatible_provider_sends_auth_without_logging_key(monkeypatch):
    monkeypatch.setenv("TEST_AI_KEY", "secret")
    seen = {}

    def request(req, timeout=None):
        seen.update(url=req.full_url, headers=req.headers)
        return _fake_response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("testbench.ai.providers.base.urlopen", request)
    provider = OpenAICompatibleProvider("http://localhost:1234/v1", api_key_env="TEST_AI_KEY", retries=0)
    assert provider.generate("p", "s", model="local") == "ok"
    assert seen["headers"]["Authorization"] == "Bearer secret"


def test_provider_reports_timeout_and_malformed_json(monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(True)
        raise TimeoutError()

    monkeypatch.setattr("testbench.ai.providers.base.urlopen", timeout)
    provider = OllamaProvider(timeout=0.01, retries=1)
    with pytest.raises(ProviderError, match="timed out"):
        provider._request_json("http://localhost:1/test", {})
    assert len(calls) == 2

    class BadResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b"not-json"

    monkeypatch.setattr("testbench.ai.providers.base.urlopen", lambda *args, **kwargs: BadResponse())
    with pytest.raises(ProviderError, match="malformed JSON"):
        OllamaProvider(retries=0)._request_json("http://localhost:1/test", {})


def test_provider_retries_rate_limit_then_succeeds(monkeypatch):
    calls = []

    def request(req, timeout=None):
        calls.append(True)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 429, "rate limited", {}, None)
        return _fake_response({"ok": True})

    monkeypatch.setattr("testbench.ai.providers.base.urlopen", request)
    monkeypatch.setattr("testbench.ai.providers.base.time.sleep", lambda _: None)
    provider = OllamaProvider(retries=1)
    assert provider._request_json("http://localhost/test", {}) == {"ok": True}
    assert len(calls) == 2


def test_ai_client_does_not_store_prompts_by_default(tmp_path: Path):
    database_path = tmp_path / "ai.sqlite"
    client = AIClient(
        {
            "enabled": True,
            "provider": "ollama",
            "model": "fake",
            "consented_providers": ["ollama"],
        },
        database_path=database_path,
        provider=FakeProvider(),
    )
    client.generate("private prompt", "system", run_id="run")
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute("SELECT prompt FROM ai_calls").fetchone()[0]
    assert stored is None


def test_ai_client_masks_ocr_text_in_images_before_provider(monkeypatch, tmp_path: Path):
    import cv2
    import numpy as np

    image = np.full((40, 100, 3), 255, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    received = {}
    provider = FakeProvider()
    original_generate = provider.generate

    def capture(prompt, system, images=None, json_schema=None, *, model=None):
        received["image"] = images[0]
        return original_generate(prompt, system, images, json_schema, model=model)

    provider.generate = capture

    class FakePytesseract:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_data(image, output_type):
            return {
                "text": ["qa@example.test"],
                "conf": ["90"],
                "left": [10],
                "top": [10],
                "width": [50],
                "height": [12],
            }

    monkeypatch.setitem(sys.modules, "pytesseract", FakePytesseract)
    client = AIClient(
        {
            "enabled": True,
            "provider": "ollama",
            "model": "fake",
            "consented_providers": ["ollama"],
        },
        database_path=tmp_path / "ai.sqlite",
        provider=provider,
    )
    client.generate("locate", "safe", images=[encoded.tobytes()])
    decoded = cv2.imdecode(np.frombuffer(received["image"], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert np.all(decoded[7:25, 7:70] == 0)

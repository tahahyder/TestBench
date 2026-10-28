import json
from pathlib import Path

import pytest

from testbench.ai.client import AIClient
from testbench.ai.healing import suggest_locator
from testbench.ai.providers.base import LLMProvider
from testbench.core.healing import non_ai_suggestions
from testbench.core.steps import Step


class FakeProvider(LLMProvider):
    def __init__(self, response):
        super().__init__(timeout=1, retries=0)
        self.response = response
        self.prompts = []

    def generate(self, prompt, system, images=None, json_schema=None, *, model=None):
        self.prompts.append((prompt, system))
        return self.response

    def health(self, model):
        return True, "ready"


class FakeDriver:
    def __init__(self, *, accepted=True, candidates=None):
        self.accepted = accepted
        self.candidates = candidates or []
        self.action = None

    def healing_candidates(self, target):
        return self.candidates

    def healing_snapshot(self):
        return {"text": "Account dashboard", "token": "private"}

    def validate_healing_locator(self, original, replacement, action):
        self.action = action
        return self.accepted


def _client(tmp_path: Path, provider):
    return AIClient(
        {
            "enabled": True,
            "provider": "ollama",
            "model": "fake",
            "consented_providers": ["ollama"],
        },
        database_path=tmp_path / "ai.sqlite",
        provider=provider,
    )


def test_ai_healing_requires_exactly_one_validated_visible_enabled_target(tmp_path):
    provider = FakeProvider(
        json.dumps(
            {
                "locator": {"role": {"role": "button", "name": "Log in"}},
                "confidence": 0.87,
            }
        )
    )
    driver = FakeDriver()
    suggestion = suggest_locator(
        driver,
        Step.model_validate({"click": "Login"}),
        client=_client(tmp_path, provider),
    )
    assert suggestion is not None
    assert suggestion.method == "ai"
    assert driver.action == "click"
    assert "private" not in provider.prompts[0][0]


def test_ai_healing_rejects_ambiguous_or_different_role_candidates(tmp_path):
    provider = FakeProvider(
        json.dumps(
            {
                "locator": {"role": {"role": "link", "name": "Dashboard"}},
                "confidence": 0.99,
            }
        )
    )
    suggestion = suggest_locator(
        FakeDriver(accepted=False),
        Step.model_validate({"click": {"role": {"role": "button", "name": "Login"}}}),
        client=_client(tmp_path, provider),
    )
    assert suggestion is None


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        '{"locator":{"xpath":"//button"},"confidence":2}',
        '{"locator":{"role":{"role":"button","name":"X"}},"confidence":0.5,"extra":1}',
    ],
)
def test_ai_healing_rejects_malformed_or_unsafe_reply(tmp_path, response):
    with pytest.raises(ValueError):
        suggest_locator(
            FakeDriver(),
            Step.model_validate({"click": "Login"}),
            client=_client(tmp_path, FakeProvider(response)),
        )


def test_non_ai_suggestions_preserve_provenance_and_confidence():
    driver = FakeDriver(
        candidates=[
            {
                "locator": {"text": "Log in"},
                "confidence": 0.8,
                "method": "fuzzy_text_label",
            }
        ]
    )
    suggestion = non_ai_suggestions(driver, Step.model_validate({"click": "Login"}))[0]
    assert suggestion.old_locator == "Login"
    assert suggestion.new_locator == {"text": "Log in"}

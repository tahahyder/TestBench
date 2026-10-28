import numpy as np
import pytest

from testbench.ai.client import AICallError
from testbench.ai.vision_assist import is_enabled, locate_description


def test_ai_vision_is_disabled_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert not is_enabled()
    with pytest.raises(AICallError, match="AI is disabled"):
        locate_description(np.zeros((40, 60, 3), dtype=np.uint8), "green button")


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content

    def generate(self, prompt, system, **kwargs):
        assert "<UNTRUSTED_TARGET>" in prompt
        assert kwargs["json_schema"]["required"] == ["bbox"]
        return self.content


def test_ai_vision_validates_and_clamps_bbox() -> None:
    assert locate_description(
        np.zeros((60, 80, 3), dtype=np.uint8),
        "green button",
        client=FakeClient('{"bbox":[-8,20,40,90]}'),
    ) == (16, 40)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"box":[1,2,3,4]}',
        '{"bbox":[1,2,-3,4]}',
        '{"bbox":[NaN,2,3,4]}',
    ],
)
def test_ai_vision_rejects_untrusted_model_output(content: str) -> None:
    with pytest.raises(ValueError):
        locate_description(
            np.zeros((60, 80, 3), dtype=np.uint8),
            "button",
            client=FakeClient(content),
        )

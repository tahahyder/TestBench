import pytest
from pydantic import ValidationError

from testbench.core.steps import Step, TestSpec


def test_yaml_style_step_is_normalized() -> None:
    step = Step.model_validate({"type": {"field": "Email", "value": "a@b.com"}})

    assert step.action == "type"
    assert step.value == {"field": "Email", "value": "a@b.com"}


def test_test_spec_accepts_all_targets() -> None:
    for target in ("web", "android", "game", "custom_target"):
        spec = TestSpec.model_validate(
            {
                "name": "sample",
                "target": target,
                "config": {"package": "com.example.app"} if target == "android" else {},
                "steps": [{"wait": 0.1}],
            }
        )
        assert spec.target == target


@pytest.mark.parametrize(
    "step",
    [
        {"click": "  "},
        {"wait": -1},
        {"type": {"field": "Email"}},
        {"unknown": "value"},
        {"click": "Sign in", "wait": 1},
    ],
)
def test_invalid_step_is_rejected(step: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(step)

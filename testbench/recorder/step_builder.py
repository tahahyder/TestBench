"""Convert optimized raw interaction events into the existing YAML step model."""

from typing import Any

from testbench.core.steps import Step
from testbench.recorder.base import RawEvent
from testbench.recorder.optimizer import optimize_events


class StepBuilder:
    """Build deterministic, validated TestBench steps from captured events."""

    def build(self, events: list[RawEvent]) -> list[Step]:
        steps = []
        for event in optimize_events(events):
            step = self._event_to_step(event)
            if step is not None:
                steps.append(step)
        return steps

    @staticmethod
    def _event_to_step(event: RawEvent) -> Step | None:
        event_type = event.type.lower()
        target = event.target
        locator = target.get("locator") or target.get("selector") or target.get("text")
        if event_type in {"navigation", "navigate"}:
            return _step("open", str(event.value))
        if event_type in {"click", "tap"}:
            if event.metadata.get("assertion"):
                return _step(
                    str(event.metadata["assertion"]),
                    locator or event.value,
                )
            if event.metadata.get("game_template"):
                return _step("click_image", str(event.metadata["game_template"]))
            if locator:
                return _step("click", locator)
            x, y = target.get("relative", (target.get("x"), target.get("y")))
            if x is not None and y is not None:
                return _step("tap", {"x": float(x), "y": float(y)})
            return None
        if event_type in {"input", "keystroke", "type"}:
            if not locator:
                return None
            value = event.value
            if event.metadata.get("sensitive"):
                value = event.metadata.get("placeholder", "${RECORDED_SECRET}")
            return _step("type", {"field": locator, "value": str(value or "")})
        if event_type in {"select", "check", "uncheck"}:
            if not locator:
                return None
            if event_type == "select":
                return _step(
                    "select_option",
                    {"field": locator, "value": str(event.value or "")},
                )
            if event.metadata.get("checked") == (event_type == "uncheck"):
                return None
            return _step("click", locator)
        if event_type in {"keypress", "key"}:
            key = str(event.value or "").lower()
            return _step("press_key", key) if key in _SUPPORTED_KEYS else None
        if event_type == "hold_key" and isinstance(event.value, dict):
            return _step(
                "hold_key",
                {
                    "key": str(event.value.get("key", "")).lower(),
                    "seconds": float(event.value.get("seconds", 0)),
                },
            )
        if event_type in {"scroll", "wheel"}:
            direction = str(event.value.get("direction", "down")) if isinstance(event.value, dict) else "down"
            amount = float(event.value.get("amount", 500)) if isinstance(event.value, dict) else 500
            return _step("scroll", {"direction": direction, "amount": amount})
        if event_type == "swipe":
            direction = str(event.value or "up")
            return _step("swipe", {"direction": direction})
        if event_type == "visible_change":
            return _step("wait", 0.2)
        if event_type == "assert_visible":
            return _step("expect_visible", locator or str(target.get("text") or ""))
        if event_type == "assert_text":
            return _step("expect_text", str(event.value or target.get("text") or ""))
        if event_type == "assert_url":
            return _step("expect_url_contains", str(event.value or ""))
        if event_type == "assert_enabled":
            return _step(
                "expect_enabled",
                {
                    "target": locator,
                    "enabled": not bool((event.value or {}).get("disabled", False)),
                },
            )
        if event_type == "screenshot":
            return _step("screenshot", str(event.value or "recorded"))
        if event_type == "wait":
            return _step("wait", float(event.value or 0))
        return None


_SUPPORTED_KEYS = {
    "enter",
    "tab",
    "escape",
    "space",
    "back",
    "home",
    "left",
    "right",
    "up",
    "down",
}


def _step(action: str, value: Any) -> Step:
    """Validate a normalized step."""
    try:
        return Step.model_validate({action: value})
    except Exception as exc:
        raise ValueError(f"Could not build recorded {action} step: {exc}") from exc

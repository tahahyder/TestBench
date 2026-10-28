"""Deterministic locator-repair helpers shared by runner and drivers."""

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from testbench.core.steps import Step


@dataclass(frozen=True)
class HealingSuggestion:
    """A candidate locator and its provenance."""

    old_locator: Any
    new_locator: Any
    confidence: float
    method: str
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def locator_for_step(step: Step) -> Any:
    """Return the locator-bearing value from a supported action."""
    if step.action in {"click", "expect_visible"}:
        return step.value
    if step.action in {"type", "select_option"} and isinstance(step.value, dict):
        return step.value.get("field")
    if step.action == "expect_enabled" and isinstance(step.value, dict):
        return step.value.get("target")
    return None


def replace_step_locator(step: Step, locator: Any) -> Step:
    """Copy one step with only its target locator replaced."""
    value = step.value
    if step.action in {"click", "expect_visible"}:
        value = locator
    elif step.action in {"type", "select_option"}:
        value = {**value, "field": locator}
    elif step.action == "expect_enabled":
        value = {**value, "target": locator}
    return Step.model_validate({step.action: value})


def is_locator_failure(error: BaseException) -> bool:
    """Recognize locator lookup failures without treating assertion failures as lookup failures."""
    message = str(error).lower()
    markers = (
        "not uniquely visible",
        "element not found",
        "was not found",
        "no element",
        "multiple matches",
        "strict mode violation",
        "could not find recorded",
        "locator resolved to",
    )
    return any(marker in message for marker in markers)


def non_ai_suggestions(driver: Any, step: Step) -> list[HealingSuggestion]:
    """Ask the driver for validated low-cost locator alternatives."""
    locator = locator_for_step(step)
    if locator is None:
        return []
    finder = getattr(driver, "healing_candidates", None)
    if not callable(finder):
        return []
    validator = getattr(driver, "validate_healing_locator", None)
    candidates = finder(locator)
    suggestions = []
    for item in candidates:
        if not isinstance(item, dict) or "locator" not in item or not 0 <= float(item.get("confidence", 0)) <= 1:
            continue
        if callable(validator) and not validator(locator, item["locator"], step.action):
            continue
        suggestions.append(
            HealingSuggestion(
                old_locator=locator,
                new_locator=item["locator"],
                confidence=float(item["confidence"]),
                method=str(item.get("method", "non_ai")),
                explanation=str(item.get("explanation", "")),
            )
        )
    return suggestions


def similar_text(first: str, second: str) -> float:
    """Return a normalized, deterministic text similarity score."""
    return SequenceMatcher(None, first.casefold().strip(), second.casefold().strip()).ratio()

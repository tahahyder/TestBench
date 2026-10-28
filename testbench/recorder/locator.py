"""Stable Playwright locator ranking and record-time uniqueness verification."""

import json
import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error, Frame, Locator

_VOLATILE_ID = re.compile(r"(?:[a-f0-9]{8,}|\d{5,}|[A-Z0-9]{14,})", re.IGNORECASE)
_UNSTABLE_WORD = re.compile(r"(?:^|[-_])[a-f0-9]{8,}(?:$|[-_])", re.IGNORECASE)


@dataclass(frozen=True)
class LocatorCandidate:
    """A ranked locator strategy and its serializable TestBench representation."""

    priority: int
    kind: str
    value: Any

    def as_step_target(self) -> dict[str, Any]:
        """Return the one-key locator mapping stored in recorded YAML."""
        return {self.kind: self.value}


def stable_identifier(value: str) -> bool:
    """Reject IDs that look generated or contain long numeric/hash segments."""
    normalized = value.strip()
    return bool(
        normalized
        and len(normalized) <= 100
        and not _VOLATILE_ID.search(normalized)
        and not _UNSTABLE_WORD.search(normalized)
    )


def score_locator_candidates(info: dict[str, Any]) -> list[LocatorCandidate]:
    """Create locator candidates in policy priority order from DOM metadata."""
    candidates: list[LocatorCandidate] = []
    role = info.get("role")
    name = _clean(info.get("name"))
    if role and name:
        candidates.append(LocatorCandidate(10, "role", {"role": role, "name": name}))
    label = _clean(info.get("label"))
    if label:
        candidates.append(LocatorCandidate(20, "label", label))
    placeholder = _clean(info.get("placeholder"))
    if placeholder:
        candidates.append(LocatorCandidate(30, "placeholder", placeholder))
    text = _clean(info.get("text"))
    if text:
        candidates.append(LocatorCandidate(40, "text", text))
    for attribute in ("data-testid", "data-test", "data-qa"):
        value = _clean(info.get(attribute))
        if value:
            candidates.append(LocatorCandidate(50, "test_id", value))
            break
    element_id = _clean(info.get("id"))
    if element_id and stable_identifier(element_id):
        candidates.append(LocatorCandidate(60, "id", element_id))
    css = _clean(info.get("css"))
    if css and len(css) <= 120 and css.count(":nth-") <= 1:
        candidates.append(LocatorCandidate(70, "css", css))
    xpath = _clean(info.get("xpath"))
    if xpath:
        candidates.append(LocatorCandidate(80, "xpath", xpath))
    return candidates


def choose_unique_locator(
    frame: Frame,
    info: dict[str, Any],
) -> LocatorCandidate | None:
    """Choose the highest-priority stable locator that uniquely identifies a node."""
    for candidate in score_locator_candidates(info):
        locator = locator_from_candidate(frame, candidate)
        try:
            if locator.count() == 1 and locator.is_visible():
                return candidate
        except Error:
            continue
    return None


def locator_from_candidate(frame: Frame, candidate: LocatorCandidate) -> Locator:
    """Build a Playwright locator for one candidate type."""
    if candidate.kind == "role":
        return frame.get_by_role(
            candidate.value["role"],
            name=candidate.value["name"],
            exact=True,
        )
    if candidate.kind == "label":
        return frame.get_by_label(candidate.value, exact=True)
    if candidate.kind == "placeholder":
        return frame.get_by_placeholder(candidate.value, exact=True)
    if candidate.kind == "text":
        return frame.get_by_text(candidate.value, exact=True)
    if candidate.kind == "test_id":
        return frame.get_by_test_id(candidate.value)
    if candidate.kind == "id":
        return frame.locator(f"[id={json.dumps(candidate.value)}]")
    if candidate.kind == "css":
        return frame.locator(candidate.value)
    if candidate.kind == "xpath":
        return frame.locator(f"xpath={candidate.value}")
    raise ValueError(f"Unknown locator strategy: {candidate.kind}")


def collect_dom_locator_info(element: dict[str, Any]) -> dict[str, Any]:
    """Normalize locator metadata supplied by the page-side event script."""
    return {
        "role": element.get("role"),
        "name": element.get("name"),
        "label": element.get("label"),
        "placeholder": element.get("placeholder"),
        "text": element.get("text"),
        "data-testid": element.get("data-testid"),
        "data-test": element.get("data-test"),
        "data-qa": element.get("data-qa"),
        "id": element.get("id"),
        "css": element.get("css"),
        "xpath": element.get("xpath"),
    }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None

"""Deterministic cleanup of raw interaction events before step construction."""

from testbench.recorder.base import RawEvent

NOISE_TYPES = {"mousemove", "mouse_move", "hover", "pointermove"}
CLICK_TYPES = {"click", "tap"}
SCROLL_TYPES = {"scroll", "wheel", "swipe"}


def optimize_events(
    events: list[RawEvent],
    *,
    duplicate_window: float = 0.45,
    scroll_window: float = 0.8,
) -> list[RawEvent]:
    """Remove noisy events, merge input/scroll bursts, and de-duplicate clicks."""
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1].timestamp, pair[0]))
    output: list[RawEvent] = []

    for _, event in ordered:
        if event.type.lower() in NOISE_TYPES:
            continue
        normalized_type = event.type.lower()
        if normalized_type in CLICK_TYPES:
            if output:
                previous = output[-1]
                same_target = _target_key(previous) == _target_key(event)
                if (
                    previous.type.lower() in CLICK_TYPES
                    and same_target
                    and 0 <= event.timestamp - previous.timestamp <= duplicate_window
                ):
                    continue
        if normalized_type in {"input", "keystroke", "type"} and output:
            previous = output[-1]
            if (
                previous.type.lower() in {"input", "keystroke", "type"}
                and _target_key(previous) == _target_key(event)
                and previous.metadata.get("sensitive") == event.metadata.get("sensitive")
            ):
                output[-1] = RawEvent(
                    type="input",
                    timestamp=event.timestamp,
                    target=event.target or previous.target,
                    value=(
                        f"{previous.value or ''}{event.value or ''}"
                        if normalized_type == "keystroke"
                        else str(event.value or "")
                    ),
                    screenshot=event.screenshot or previous.screenshot,
                    metadata={**previous.metadata, **event.metadata},
                )
                continue
        if normalized_type in SCROLL_TYPES and output:
            previous = output[-1]
            if previous.type.lower() in SCROLL_TYPES and event.timestamp - previous.timestamp <= scroll_window:
                if (
                    isinstance(previous.value, dict)
                    and isinstance(event.value, dict)
                    and previous.value.get("direction") == event.value.get("direction")
                ):
                    value = {
                        **previous.value,
                        "amount": float(previous.value.get("amount", 0)) + float(event.value.get("amount", 0)),
                    }
                else:
                    value = event.value
                output[-1] = RawEvent(
                    type=event.type,
                    timestamp=event.timestamp,
                    target=event.target or previous.target,
                    value=value,
                    screenshot=event.screenshot or previous.screenshot,
                    metadata={**previous.metadata, **event.metadata},
                )
                continue
        output.append(event)
    return output


def _target_key(event: RawEvent) -> tuple:
    target = event.target
    return (
        target.get("locator") or target.get("selector") or target.get("text"),
        target.get("role"),
        target.get("name"),
    )

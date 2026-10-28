"""Shared recorder lifecycle and immutable raw interaction events."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testbench.core.steps import Step


@dataclass(frozen=True)
class RawEvent:
    """Unprocessed user interaction captured from one application surface."""

    type: str
    timestamp: float
    target: dict[str, Any] = field(default_factory=dict)
    value: Any = None
    screenshot: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for the live recorder API."""
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "target": self.target,
            "value": self.value,
            "screenshot": self.screenshot,
            "metadata": self.metadata,
        }


EventCallback = Callable[[dict[str, Any]], None]


class Recorder(ABC):
    """Lifecycle interface shared by browser, Android, and game recorders."""

    def __init__(self, *, on_event: EventCallback | None = None) -> None:
        self.on_event = on_event
        self.paused = False
        self.raw_events: list[RawEvent] = []
        self.warnings: list[str] = []
        self.variables: list[str] = []

    @abstractmethod
    def start(self, config: dict[str, Any]) -> None:
        """Start recording interactions for the supplied target configuration."""

    @abstractmethod
    def stop(self) -> list[Step]:
        """Stop capture and convert raw events to normalized test steps."""

    @abstractmethod
    def pause(self) -> None:
        """Temporarily stop accepting captured user interactions."""

    @abstractmethod
    def resume(self) -> None:
        """Resume accepting captured user interactions."""

    def emit(self, event: RawEvent) -> None:
        """Store an event and notify listeners without coupling conversion to capture."""
        if self.paused:
            return
        self.raw_events.append(event)
        if self.on_event is not None:
            self.on_event(
                {
                    "kind": "raw_event",
                    "event": event.to_dict(),
                    "step_count": len(self.raw_events),
                }
            )

    def capture_screenshot(self, path: Path) -> str:
        """Capture a target screenshot when a concrete recorder supports it."""
        raise NotImplementedError(f"{type(self).__name__} does not expose screenshot capture.")

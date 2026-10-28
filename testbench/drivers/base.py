"""Shared interface for TestBench automation drivers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Driver(ABC):
    """Target-independent operations required by the test runner."""

    def __init__(self, config: dict[str, Any], artifact_dir: Path) -> None:
        self.config = config
        self.artifact_dir = artifact_dir

    @abstractmethod
    def open(self, destination: str) -> None:
        """Open a URL, app, or target-specific destination."""

    @abstractmethod
    def click(self, target: str | dict[str, str]) -> None:
        """Activate a visible control."""

    @abstractmethod
    def type(self, field: str, value: str) -> None:
        """Enter text into a field."""

    @abstractmethod
    def wait(self, seconds: float) -> None:
        """Wait for a specified duration."""

    @abstractmethod
    def screenshot(self, path: Path) -> None:
        """Save a screenshot to the requested path."""

    @abstractmethod
    def expect_visible(self, target: str) -> None:
        """Raise an exception unless the target is visible."""

    @abstractmethod
    def expect_text(self, text: str) -> None:
        """Raise an exception unless the text is visible."""

    @abstractmethod
    def close(self) -> None:
        """Release target resources and finish recordings."""

    def swipe(self, direction: str) -> None:
        """Perform an optional target-specific swipe."""
        raise NotImplementedError(f"{type(self).__name__} does not support swipe.")

    def scroll_to(self, text: str) -> None:
        """Perform an optional target-specific scroll to text."""
        raise NotImplementedError(f"{type(self).__name__} does not support scroll_to.")

    def back(self) -> None:
        """Perform an optional target-specific back navigation."""
        raise NotImplementedError(f"{type(self).__name__} does not support back.")

    def home(self) -> None:
        """Perform an optional target-specific home navigation."""
        raise NotImplementedError(f"{type(self).__name__} does not support home.")

    def hide_keyboard(self) -> None:
        """Hide an optional target-specific keyboard."""
        raise NotImplementedError(f"{type(self).__name__} does not support hide_keyboard.")

    def press_key(self, key: str) -> None:
        """Press an optional target-specific key."""
        raise NotImplementedError(f"{type(self).__name__} does not support press_key.")

    def set_orientation(self, orientation: str) -> None:
        """Set an optional target-specific orientation."""
        raise NotImplementedError(f"{type(self).__name__} does not support set_orientation.")

"""Small target-independent interface for game capture and input."""

from abc import ABC, abstractmethod

import numpy as np


class Surface(ABC):
    """A screenshot source and input destination for a game."""

    @abstractmethod
    def capture(self) -> np.ndarray:
        """Return the current screen as a BGR image."""

    @abstractmethod
    def click(self, x: int, y: int) -> None:
        """Click an absolute point in surface coordinates."""

    @abstractmethod
    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        """Drag from one absolute point to another."""

    @abstractmethod
    def key_down(self, key: str) -> None:
        """Hold a key."""

    @abstractmethod
    def key_up(self, key: str) -> None:
        """Release a key."""

    @abstractmethod
    def press(self, key: str) -> None:
        """Press and release a key."""

    @abstractmethod
    def size(self) -> tuple[int, int]:
        """Return surface width and height."""

    def type_text(self, text: str) -> None:
        """Optionally type text into the active game surface."""
        raise NotImplementedError(f"{type(self).__name__} cannot type text.")

    def close(self) -> None:
        """Release resources used by the surface."""

"""In-memory surface for deterministic game-driver tests."""

from collections.abc import Callable

import numpy as np

from testbench.drivers.surfaces.base import Surface


class FakeSurface(Surface):
    """Surface returning test frames and recording input operations."""

    def __init__(
        self,
        frames: list[np.ndarray] | None = None,
        *,
        on_click: Callable[[int, int], None] | None = None,
    ) -> None:
        if not frames:
            raise ValueError("FakeSurface needs at least one frame.")
        self.frames = frames
        self.frame_index = 0
        self.on_click = on_click
        self.actions: list[tuple] = []

    def capture(self) -> np.ndarray:
        frame = self.frames[min(self.frame_index, len(self.frames) - 1)]
        if self.frame_index < len(self.frames) - 1:
            self.frame_index += 1
        return frame.copy()

    def click(self, x: int, y: int) -> None:
        self.actions.append(("click", x, y))
        if self.on_click:
            self.on_click(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        self.actions.append(("drag", x1, y1, x2, y2, duration))

    def key_down(self, key: str) -> None:
        self.actions.append(("key_down", key))

    def key_up(self, key: str) -> None:
        self.actions.append(("key_up", key))

    def press(self, key: str) -> None:
        self.actions.append(("press", key))

    def type_text(self, text: str) -> None:
        self.actions.append(("type_text", text))

    def size(self) -> tuple[int, int]:
        frame = self.frames[0]
        return frame.shape[1], frame.shape[0]

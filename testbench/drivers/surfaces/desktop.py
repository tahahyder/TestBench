"""mss and PyAutoGUI surface for an existing desktop game window."""

from typing import Any

import mss
import numpy as np
import pyautogui

from testbench.drivers.surfaces.base import Surface


class DesktopSurface(Surface):
    """Capture a monitor or a window selected by its title."""

    def __init__(self, window_title: str | None = None) -> None:
        self.window_title = window_title
        self._capture = mss.mss()
        self._window: Any = None
        self._region: dict[str, int] | None = None

    def open(self) -> None:
        if self.window_title:
            try:
                import pygetwindow
            except ImportError as exc:
                raise RuntimeError(
                    "Finding a window by title requires pygetwindow. "
                    "Install it with `python -m pip install pygetwindow`."
                ) from exc
            windows = [
                window
                for window in pygetwindow.getAllWindows()
                if self.window_title.casefold() in window.title.casefold()
            ]
            if not windows:
                raise LookupError(f"No desktop window contains title {self.window_title!r}.")
            window = windows[0]
            if window.width <= 0 or window.height <= 0 or window.isMinimized:
                raise RuntimeError(f"Game window {window.title!r} is minimized or has no visible area.")
            window.activate()
            self._window = window
            self._region = {
                "left": window.left,
                "top": window.top,
                "width": window.width,
                "height": window.height,
            }
        else:
            self._region = self._capture.monitors[1]

    def capture(self) -> np.ndarray:
        region = self._require_region()
        frame = np.asarray(self._capture.grab(region))
        return frame[:, :, :3][:, :, ::-1].copy()

    def click(self, x: int, y: int) -> None:
        region = self._require_region()
        pyautogui.click(region["left"] + x, region["top"] + y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        region = self._require_region()
        pyautogui.moveTo(region["left"] + x1, region["top"] + y1)
        pyautogui.dragTo(
            region["left"] + x2,
            region["top"] + y2,
            duration=duration,
            button="left",
        )

    def key_down(self, key: str) -> None:
        pyautogui.keyDown(_pyautogui_key(key))

    def key_up(self, key: str) -> None:
        pyautogui.keyUp(_pyautogui_key(key))

    def press(self, key: str) -> None:
        pyautogui.press(_pyautogui_key(key))

    def type_text(self, text: str) -> None:
        pyautogui.write(text)

    def size(self) -> tuple[int, int]:
        region = self._require_region()
        return region["width"], region["height"]

    def close(self) -> None:
        self._capture.close()

    def _require_region(self) -> dict[str, int]:
        if self._region is None:
            raise RuntimeError("Attach to a desktop window before using its surface.")
        return self._region


def _pyautogui_key(key: str) -> str:
    return {"space": "space", "left": "left", "right": "right", "up": "up", "down": "down"}.get(key.lower(), key)

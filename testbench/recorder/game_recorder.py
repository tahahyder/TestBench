"""Basic game recorder that turns observed clicks into local image templates."""

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from testbench.core.steps import Step
from testbench.drivers.devices import DeviceManager
from testbench.drivers.surfaces.android import AndroidSurface
from testbench.drivers.surfaces.desktop import DesktopSurface
from testbench.recorder.base import RawEvent, Recorder
from testbench.recorder.mobile_recorder import _scale_touch
from testbench.recorder.step_builder import StepBuilder
from testbench.recorder.web_recorder import WebRecorder


class GameRecorder(Recorder):
    """Observe desktop, Android, or browser game input and save click templates."""

    def __init__(self, *, on_event=None) -> None:
        super().__init__(on_event=on_event)
        self.surface: str = "desktop"
        self.artifact_dir = Path("runs") / "recording"
        self.templates_dir = Path("templates")
        self.crop_size = 80
        self._surface: Any = None
        self._mouse_listener: Any = None
        self._keyboard_listener: Any = None
        self._getevent: subprocess.Popen | None = None
        self._touch_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._touch_x: int | None = None
        self._touch_y: int | None = None
        self._held_keys: dict[str, float] = {}
        self._touch_ranges: tuple[int, int, int, int] | None = None
        self._dimensions = (1, 1)
        self._keyboard_pressed_at: dict[str, float] = {}
        self._web: WebRecorder | None = None
        self._web_callback_index = 0

    def start(self, config: dict[str, Any]) -> None:
        self.surface = str(config.get("surface", "desktop"))
        if self.surface not in {"desktop", "android", "browser"}:
            raise ValueError("Game recorder surface must be desktop, android, or browser.")
        self.artifact_dir = Path(config.get("artifact_dir", "runs/recording"))
        self.templates_dir = Path(config.get("templates_dir", "templates"))
        self.crop_size = int(config.get("crop_size", 80))
        if not 16 <= self.crop_size <= 512:
            raise ValueError("Game click crop_size must be between 16 and 512.")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        if self.surface == "browser":
            web_config = {**config, "artifact_dir": str(self.artifact_dir), "headless": False}
            self._web = WebRecorder(on_event=self._on_web_event)
            self._web.start(web_config)
        elif self.surface == "desktop":
            try:
                from pynput import keyboard, mouse
            except ImportError as exc:
                raise RuntimeError(
                    "Desktop game recording requires pynput. Install it with `python -m pip install pynput`."
                ) from exc
            self._surface = DesktopSurface(config.get("window_title"))
            self._surface.open()
            listener = mouse.Listener(on_click=self._on_desktop_click)
            listener.start()
            self._mouse_listener = listener
            key_listener = keyboard.Listener(
                on_press=self._on_key_down,
                on_release=self._on_key_up,
            )
            key_listener.start()
            self._keyboard_listener = key_listener
        else:
            self._surface = AndroidSurface(str(config.get("device", "auto")), self.artifact_dir)
            self._surface.open()
            self._dimensions = self._surface.size()
            manager = DeviceManager()
            self._touch_ranges = manager.touch_axis_ranges(self._surface.serial or "")
            if manager.adb_path and self._surface.serial:
                self._getevent = subprocess.Popen(
                    [manager.adb_path, "-s", self._surface.serial, "shell", "getevent", "-lt"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                self._touch_thread = threading.Thread(
                    target=self._read_android_touch,
                    name="testbench-game-touch",
                    daemon=True,
                )
                self._touch_thread.start()

    def stop(self) -> list[Step]:
        self._stop_event.set()
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener.join(timeout=3)
            self._mouse_listener = None
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener.join(timeout=3)
            self._keyboard_listener = None
        if self._getevent is not None:
            self._getevent.terminate()
            try:
                self._getevent.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._getevent.kill()
                self._getevent.wait(timeout=2)
            self._getevent = None
        if self._touch_thread is not None:
            self._touch_thread.join(timeout=2)
            self._touch_thread = None
        if self._web is not None:
            self._web.stop()
            self._web = None
        if self._surface is not None:
            self._surface.close()
            self._surface = None
        return StepBuilder().build(self.raw_events)

    def pause(self) -> None:
        self.paused = True
        if self._web is not None:
            self._web.pause()

    def resume(self) -> None:
        self.paused = False
        if self._web is not None:
            self._web.resume()

    def _on_desktop_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if not pressed or self.paused or str(button) != "Button.left":
            return
        region = self._surface._require_region()
        self._save_click(int(x - region["left"]), int(y - region["top"]))

    def _read_android_touch(self) -> None:
        process = self._getevent
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if self._stop_event.is_set():
                return
            match = re.search(r"EV_ABS\s+ABS_MT_POSITION_([XY])\s+([0-9a-fA-F]+)", line)
            if match:
                coordinate = int(match.group(2), 16)
                if match.group(1) == "X":
                    self._touch_x = coordinate
                else:
                    self._touch_y = coordinate
            elif "BTN_TOUCH" in line and "UP" in line:
                if self._touch_x is not None and self._touch_y is not None:
                    x, y = _scale_touch(
                        self._touch_x,
                        self._touch_y,
                        self._touch_ranges,
                        self._dimensions,
                    )
                    self._save_click(x, y)
            elif "EV_KEY" in line and ("KEY_BACK" in line or "KEY_HOME" in line):
                if "DOWN" in line or "00000001" in line:
                    value = "back" if "KEY_BACK" in line else "home"
                    self.emit(RawEvent("keypress", time.monotonic(), value=value))
            elif "EV_KEY" in line:
                match = re.search(
                    r"EV_KEY\s+(KEY_[A-Z0-9_]+)\s+(DOWN|UP|0000000[01])",
                    line,
                )
                if match:
                    value = _android_key_name(match.group(1))
                    timestamp = time.monotonic()
                    if match.group(2) in {"DOWN", "00000001"}:
                        self._keyboard_pressed_at[value] = timestamp
                    else:
                        started = self._keyboard_pressed_at.pop(value, None)
                        if started is not None:
                            duration = timestamp - started
                            if duration >= 0.5:
                                self.emit(
                                    RawEvent(
                                        "hold_key",
                                        timestamp,
                                        value={"key": value, "seconds": round(duration, 2)},
                                    )
                                )
                            else:
                                self.emit(RawEvent("keypress", timestamp, value=value))

    def _on_key_down(self, key: Any) -> None:
        normalized = _key_name(key)
        if normalized and normalized not in self._held_keys:
            self._held_keys[normalized] = time.monotonic()

    def _on_key_up(self, key: Any) -> None:
        normalized = _key_name(key)
        started = self._held_keys.pop(normalized, None) if normalized else None
        if started is None or self.paused:
            return
        duration = time.monotonic() - started
        if duration >= 0.5:
            self.emit(
                RawEvent(
                    "hold_key",
                    time.monotonic(),
                    value={"key": normalized, "seconds": round(duration, 2)},
                )
            )
        else:
            self.emit(RawEvent("keypress", time.monotonic(), value=normalized))

    def _on_web_event(self, message: dict[str, Any]) -> None:
        if self.paused or message.get("kind") != "raw_event":
            return
        event = message.get("event", {})
        if event.get("type") == "click":
            target = event.get("target", {})
            x, y = target.get("x"), target.get("y")
            screenshot = event.get("screenshot")
            if x is not None and y is not None and screenshot:
                self._save_click(int(x), int(y), cv2.imread(screenshot))
        elif event.get("type") == "keypress":
            self.emit(
                RawEvent(
                    "keypress",
                    time.monotonic(),
                    value=event.get("value"),
                    screenshot=event.get("screenshot"),
                )
            )

    def _save_click(self, x: int, y: int, frame: np.ndarray | None = None) -> None:
        if self.paused:
            return
        if frame is None:
            if self._surface is None:
                return
            frame = self._surface.capture()
        height, width = frame.shape[:2]
        half = self.crop_size // 2
        left, top = max(0, x - half), max(0, y - half)
        right, bottom = min(width, x + half), min(height, y + half)
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            self.warnings.append(f"Ignored click outside the captured screen at ({x}, {y}).")
            return
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        existing = [
            int(match.group(1))
            for path in self.templates_dir.glob("click_*.png")
            if (match := re.fullmatch(r"click_(\d+)\.png", path.name))
        ]
        index = max(existing, default=0) + 1
        name = f"click_{index:03d}.png"
        destination = self.templates_dir / name
        if not cv2.imwrite(str(destination), crop):
            raise OSError(f"Could not save game click template to {destination}.")
        screenshot_path = None
        if self._surface is not None:
            screenshot = self.artifact_dir / "screenshots" / f"click_{index:03d}.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            if cv2.imwrite(str(screenshot), frame):
                screenshot_path = str(screenshot.resolve())
        self.emit(
            RawEvent(
                "click",
                time.monotonic(),
                target={"x": x, "y": y},
                screenshot=screenshot_path,
                metadata={"game_template": name},
            )
        )


def _key_name(key: Any) -> str | None:
    value = getattr(key, "char", None) or getattr(key, "name", None)
    if value is None:
        value = str(key).replace("Key.", "")
    aliases = {
        "space": "space",
        "left": "left",
        "right": "right",
        "up": "up",
        "down": "down",
        "enter": "enter",
        "esc": "escape",
    }
    return aliases.get(str(value).lower(), str(value).lower())


def _android_key_name(key: str) -> str:
    aliases = {
        "KEY_BACK": "back",
        "KEY_HOME": "home",
        "KEY_ENTER": "enter",
        "KEY_SPACE": "space",
        "KEY_LEFT": "left",
        "KEY_RIGHT": "right",
        "KEY_UP": "up",
        "KEY_DOWN": "down",
        "KEY_ESC": "escape",
    }
    return aliases.get(key, key.removeprefix("KEY_").lower())

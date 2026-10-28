"""Android recorder using Appium hierarchy polling and adb touch diagnostics."""

import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import cv2

from testbench.core.steps import Step
from testbench.drivers.devices import DeviceManager
from testbench.drivers.mobile import MobileDriver, parse_page_source
from testbench.recorder.base import RawEvent, Recorder
from testbench.recorder.step_builder import StepBuilder

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class AndroidRecorder(Recorder):
    """Capture Android UI focus/text changes; adb touch events are best-effort."""

    def __init__(self, *, on_event=None) -> None:
        super().__init__(on_event=on_event)
        self.driver: MobileDriver | None = None
        self._stop_event = threading.Event()
        self._poller: threading.Thread | None = None
        self._touch_thread: threading.Thread | None = None
        self._getevent: subprocess.Popen | None = None
        self.artifact_dir = Path("runs") / "recording"
        self._previous: dict[str, dict[str, Any]] = {}
        self._touch: tuple[int, int] | None = None
        self._touch_start: tuple[int, int] | None = None
        self._touch_active = False
        self._touch_ranges: tuple[int, int, int, int] | None = None
        self._dimensions = (1, 1)
        self._appium_lock = threading.RLock()

    def start(self, config: dict[str, Any]) -> None:
        self.artifact_dir = Path(config.get("artifact_dir", "runs/recording"))
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        recorder_config = {
            **config,
            "record_screen": False,
            "capture_logcat": False,
        }
        self.driver = MobileDriver(recorder_config, self.artifact_dir)
        self.driver.open("")
        width, height = self.driver._require_driver().get_window_size().values()
        self._dimensions = (int(width), int(height))
        manager = DeviceManager()
        try:
            self._touch_ranges = manager.touch_axis_ranges(self.driver._serial or "")
        except RuntimeError as exc:
            self._touch_ranges = None
            self.warnings.append(str(exc))
        if self._touch_ranges is None:
            self.warnings.append("Could not read touch-axis ranges; raw adb coordinates will be used.")
        if manager.adb_path and self.driver._serial:
            try:
                self._getevent = subprocess.Popen(
                    [manager.adb_path, "-s", self.driver._serial, "shell", "getevent", "-lt"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                self._touch_thread = threading.Thread(
                    target=self._read_touch_events,
                    name="testbench-android-touch",
                    daemon=True,
                )
                self._touch_thread.start()
            except OSError as exc:
                self.warnings.append(f"Could not monitor adb touch events: {exc}")
        self._previous = _hierarchy_map(self.driver._require_driver().page_source)
        self._poller = threading.Thread(
            target=self._poll_hierarchy,
            name="testbench-android-hierarchy",
            daemon=True,
        )
        self._poller.start()

    def stop(self) -> list[Step]:
        self._stop_event.set()
        if self._poller is not None:
            self._poller.join(timeout=3)
        if self._touch_thread is not None:
            self._touch_thread.join(timeout=3)
        if self._getevent is not None:
            self._getevent.terminate()
            try:
                self._getevent.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._getevent.kill()
                self._getevent.wait(timeout=2)
            self._getevent = None
        if self.driver is not None:
            self.driver.close()
            self.driver = None
        return StepBuilder().build(self.raw_events)

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def _read_touch_events(self) -> None:
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
                    old_y = self._touch[1] if self._touch else 0
                    self._touch = (coordinate, old_y)
                else:
                    old_x = self._touch[0] if self._touch else 0
                    self._touch = (old_x, coordinate)
                if self._touch_active and self._touch_start is None:
                    self._touch_start = self._touch
                continue
            if "BTN_TOUCH" in line and "DOWN" in line:
                self._touch_active = True
                self._touch_start = self._touch
            elif "BTN_TOUCH" in line and "UP" in line:
                point = self._touch
                start = self._touch_start
                self._touch = None
                self._touch_start = None
                self._touch_active = False
                if point and start and _is_swipe(start, point, self._dimensions):
                    self._record_swipe(start, point)
                elif point:
                    self._record_tap(*point)
            elif "EV_KEY" in line and ("KEY_BACK" in line or "KEY_HOME" in line):
                if "DOWN" in line or "00000001" in line:
                    key = "back" if "KEY_BACK" in line else "home"
                    self.emit(RawEvent("keypress", time.monotonic(), value=key))

    def _record_tap(self, x: int, y: int) -> None:
        if self.paused or self.driver is None:
            return
        x, y = _scale_touch(x, y, self._touch_ranges, self._dimensions)
        screenshot = self.artifact_dir / "screenshots" / f"tap_{len(self.raw_events) + 1:04d}.png"
        try:
            with self._appium_lock:
                source = self.driver._require_driver().page_source
                nodes = _elements_at_point(source, x, y, *self._dimensions)
                self._capture_snapshot(screenshot, source)
        except Exception as exc:
            self.warnings.append(f"Tap screenshot failed: {exc}")
            screenshot_path = None
            nodes = []
        else:
            screenshot_path = str(screenshot.resolve())
        if nodes:
            element = nodes[0]
            locator = _mobile_locator(element)
            target = {"locator": locator, "text": element.get("text", "")}
            event = RawEvent("click", time.monotonic(), target=target, screenshot=screenshot_path)
        else:
            self.warnings.append("Could not map an Android touch to a UI element; recorded relative coordinates.")
            target = {
                "relative": [
                    min(1.0, max(0.0, x / self._dimensions[0])),
                    min(1.0, max(0.0, y / self._dimensions[1])),
                ]
            }
            event = RawEvent("tap", time.monotonic(), target=target, screenshot=screenshot_path)
        self.emit(event)

    def _record_swipe(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        if abs(dx) > abs(dy):
            direction = "right" if dx > 0 else "left"
        else:
            direction = "down" if dy > 0 else "up"
        self.emit(RawEvent("swipe", time.monotonic(), value=direction))

    def _poll_hierarchy(self) -> None:
        while not self._stop_event.wait(0.4):
            if self.paused or self.driver is None:
                continue
            try:
                with self._appium_lock:
                    current = _hierarchy_map(self.driver._require_driver().page_source)
            except Exception as exc:
                self.warnings.append(f"Android hierarchy polling failed: {exc}")
                continue
            for key, element in current.items():
                previous = self._previous.get(key)
                if element.get("focused") and element.get("text") != (previous or {}).get("text"):
                    self._record_text_change(element)
            self._previous = current

    def _record_text_change(self, element: dict[str, Any]) -> None:
        text = element.get("text", "")
        if not text:
            return
        sensitive = any(
            word in " ".join((element.get("resource-id", ""), element.get("content-desc", ""))).lower()
            for word in ("password", "token", "secret", "otp", "cvv", "card")
        )
        sensitive = sensitive or element.get("password", "").lower() == "true"
        locator = _mobile_locator(element)
        metadata = {"sensitive": sensitive}
        value = text
        if sensitive:
            name = re.sub(r"\W+", "_", element.get("resource-id", "RECORDED_SECRET")).strip("_").upper()
            name = name or "RECORDED_SECRET"
            metadata["placeholder"] = f"${{{name}}}"
            value = ""
            if name not in self.variables:
                self.variables.append(name)
        screenshot = self.artifact_dir / "screenshots" / f"input_{len(self.raw_events) + 1:04d}.png"
        try:
            with self._appium_lock:
                source = self.driver._require_driver().page_source
                self._capture_snapshot(screenshot, source)
            path = str(screenshot.resolve())
        except Exception as exc:
            self.warnings.append(f"Input screenshot failed: {exc}")
            path = None
        self.emit(
            RawEvent(
                "input",
                time.monotonic(),
                target={"locator": locator},
                value=value,
                screenshot=path,
                metadata=metadata,
            )
        )

    def _capture_snapshot(self, path: Path, source: str) -> None:
        if self.driver is None:
            raise RuntimeError("Android recorder is not active.")
        self.driver.screenshot(path)
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            raise OSError(f"Could not decode Android screenshot {path}.")
        for element in parse_page_source(source):
            if not _is_sensitive_element(element):
                continue
            bounds = _BOUNDS.fullmatch(element.get("bounds", ""))
            if not bounds:
                continue
            left, top, right, bottom = map(int, bounds.groups())
            left = max(0, min(frame.shape[1], left))
            right = max(0, min(frame.shape[1], right))
            top = max(0, min(frame.shape[0], top))
            bottom = max(0, min(frame.shape[0], bottom))
            if right > left and bottom > top:
                frame[top:bottom, left:right] = (0, 0, 0)
        if not cv2.imwrite(str(path), frame):
            raise OSError(f"Could not redact Android screenshot {path}.")


def _hierarchy_map(source: str) -> dict[str, dict[str, Any]]:
    root = ET.fromstring(source)
    result = {}
    for node in root.iter():
        attrs = node.attrib
        resource_id = attrs.get("resource-id", "")
        bounds = attrs.get("bounds", "")
        if not (resource_id or attrs.get("focused") == "true"):
            continue
        result[resource_id or bounds] = attrs.copy()
    return result


def _elements_at_point(source: str, x: int, y: int, width: int, height: int) -> list[dict[str, Any]]:
    elements = parse_page_source(source)
    matches = []
    for element in elements:
        bounds = _BOUNDS.fullmatch(element.get("bounds", ""))
        if not bounds or not element.get("clickable"):
            continue
        left, top, right, bottom = map(int, bounds.groups())
        if left <= x <= right and top <= y <= bottom:
            matches.append(element)
    return sorted(matches, key=lambda element: _area(element.get("bounds", "")))


def _area(bounds: str) -> int:
    match = _BOUNDS.fullmatch(bounds)
    if not match:
        return 2**31
    left, top, right, bottom = map(int, match.groups())
    return max(0, right - left) * max(0, bottom - top)


def _is_swipe(
    start: tuple[int, int],
    end: tuple[int, int],
    dimensions: tuple[int, int],
) -> bool:
    threshold = max(40, min(dimensions) * 0.08)
    return abs(end[0] - start[0]) > threshold or abs(end[1] - start[1]) > threshold


def _scale_touch(
    x: int,
    y: int,
    ranges: tuple[int, int, int, int] | None,
    dimensions: tuple[int, int],
) -> tuple[int, int]:
    if ranges is None:
        return x, y
    min_x, max_x, min_y, max_y = ranges
    width, height = dimensions
    scaled_x = round((x - min_x) * (width - 1) / (max_x - min_x))
    scaled_y = round((y - min_y) * (height - 1) / (max_y - min_y))
    return (
        min(width - 1, max(0, scaled_x)),
        min(height - 1, max(0, scaled_y)),
    )


def _mobile_locator(element: dict[str, Any]) -> dict[str, str] | str:
    for field, key in (
        ("content_desc", "accessibility_id"),
        ("text", "text"),
        ("id", "id"),
    ):
        value = element.get(field)
        if value:
            if key == "text":
                return str(value)
            return {key: str(value)}
    class_name = str(element.get("class", ""))
    bounds = str(element.get("bounds", ""))
    if class_name and bounds:
        return {"xpath": f"//*[@class='{class_name}' and @bounds='{bounds}']"}
    return str(element.get("text") or class_name)


def _is_sensitive_element(element: dict[str, Any]) -> bool:
    descriptor = " ".join(
        (
            element.get("id", ""),
            element.get("content_desc", ""),
            element.get("class", ""),
        )
    ).lower()
    return bool(element.get("password")) or any(
        word in descriptor for word in ("password", "token", "secret", "otp", "cvv", "card")
    )

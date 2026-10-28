"""Appium adapter for Android emulators and USB devices."""

import base64
import json
import subprocess
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    InvalidSelectorException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.remote.webdriver import WebDriver as AppiumWebDriver
from selenium.webdriver.support.ui import WebDriverWait

from testbench.core.steps import AndroidConfig
from testbench.drivers.base import Driver
from testbench.drivers.devices import DeviceManager

LOCATOR_KEYS = {
    "id": AppiumBy.ID,
    "xpath": AppiumBy.XPATH,
    "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
}


class MobileDriver(Driver):
    """Android adapter with explicit locator waits and per-run diagnostics."""

    def __init__(self, config: dict[str, Any], artifact_dir: Path) -> None:
        super().__init__(config, artifact_dir)
        self._record_screen = bool(config.get("record_screen", True))
        self._capture_logcat = bool(config.get("capture_logcat", True))
        try:
            settings_config = {
                key: value for key, value in config.items() if key not in {"record_screen", "capture_logcat"}
            }
            self.settings = AndroidConfig.model_validate(settings_config)
        except ValueError as exc:
            raise ValueError(f"Invalid Android config: {exc}") from exc
        self._driver: AppiumWebDriver | None = None
        self._logcat_process: subprocess.Popen | None = None
        self._logcat_file = None
        self._recording_started = False
        self._serial: str | None = None
        self.metadata: dict[str, Any] = {}

    def open(self, destination: str) -> None:
        """Create an Appium session and open the configured installed app or APK."""
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        device_manager = DeviceManager()
        self._serial, device_info = self._select_device(device_manager)

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.set_capability("appium:deviceName", self._serial)
        options.set_capability("appium:udid", self._serial)
        options.set_capability("appium:newCommandTimeout", 120)
        options.set_capability(
            "appium:noReset",
            not self.settings.reset,
        )

        apk_path = self._apk_path(destination)
        if apk_path is not None:
            options.set_capability("appium:app", str(apk_path))
            if self.settings.reset:
                options.set_capability("appium:fullReset", True)
        else:
            options.set_capability("appium:appPackage", self.settings.package)
            if self.settings.activity:
                options.set_capability("appium:appActivity", self.settings.activity)

        options.set_capability(
            "appium:uiautomator2ServerLaunchTimeout",
            60_000,
        )
        if self._capture_logcat:
            self._start_logcat(device_manager.adb_path)
        try:
            self._driver = webdriver.Remote(
                command_executor=self.settings.appium_url,
                options=options,
            )
            self._driver.implicitly_wait(self.settings.timeouts.implicit)
            self.metadata = {
                "device_model": (device_info or {}).get("model", ""),
                "android_version": (device_info or {}).get("android_version", ""),
                "device_serial": self._serial,
                "app_package": self.settings.package or "",
            }
            if not apk_path and self.settings.package:
                self.metadata["app_package"] = self.settings.package
            else:
                self.metadata["app_package"] = str(getattr(self._driver, "current_package", "") or "")
            if self._record_screen:
                self._driver.start_recording_screen()
                self._recording_started = True
        except Exception as exc:
            try:
                self.close()
            except Exception as close_error:
                exc.add_note(f"Android session cleanup failed: {close_error}")
            raise

    def click(self, target: str | dict[str, str]) -> None:
        self._find_element(target).click()

    def tap(self, point: dict[str, float] | list[float] | tuple[float, float]) -> None:
        """Tap absolute or normalized screen coordinates from a recorded step."""
        if isinstance(point, dict):
            x, y = float(point["x"]), float(point["y"])
        else:
            x, y = float(point[0]), float(point[1])
        driver = self._require_driver()
        size = driver.get_window_size()
        if 0 <= x <= 1 and 0 <= y <= 1:
            x, y = x * size["width"], y * size["height"]
        driver.execute_script(
            "mobile: clickGesture",
            {"x": round(x), "y": round(y)},
        )

    def type(self, field: str | dict[str, str], value: str) -> None:
        element = self._find_element(field)
        element.clear()
        element.send_keys(value)

    def wait(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("wait duration must not be negative.")
        time.sleep(seconds)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self._require_driver().save_screenshot(str(path)):
            raise OSError(f"Appium did not save screenshot to {path}.")
        try:
            page_source = self._require_driver().page_source
        except WebDriverException:
            return
        source_path = path.with_suffix(".xml")
        source_path.write_text(_redact_sensitive_xml(page_source), encoding="utf-8")

    def inspect(self, screenshot_path: Path) -> dict[str, Any]:
        """Capture a connected app's current screenshot and element hierarchy."""
        self.screenshot(screenshot_path)
        return {
            "screenshot_path": str(screenshot_path),
            "page_source": self._require_driver().page_source,
            "elements": parse_page_source(self._require_driver().page_source),
            "metadata": self.metadata.copy(),
        }

    def expect_visible(self, target: str | dict[str, str]) -> None:
        element = self._find_element(target)
        try:
            WebDriverWait(
                self._require_driver(),
                self.settings.timeouts.explicit,
                poll_frequency=min(0.2, self.settings.timeouts.explicit),
            ).until(lambda _: element if element.is_displayed() else False)
        except TimeoutException as exc:
            raise AssertionError(f"Expected {target!r} to be visible.") from exc

    def expect_text(self, text: str) -> None:
        self.expect_visible(text)

    def healing_candidates(self, target: str | dict[str, str]) -> list[dict[str, Any]]:
        """Find similar visible Android labels or accessibility descriptions."""
        description = _mobile_target_text(target)
        if not description:
            return []
        source = self._require_driver().page_source
        candidates = []
        for element in parse_page_source(source):
            if not element.get("text") and not element.get("content_desc"):
                continue
            element_class = element.get("class", "")
            if element_class.endswith("EditText") and element.get("text") is None:
                continue
            value = element.get("content_desc") or element.get("text")
            confidence = SequenceMatcher(None, description.casefold().strip(), value.casefold().strip()).ratio()
            if confidence < 0.62:
                continue
            resource_id = element.get("id")
            locator = (
                {"accessibility_id": element["content_desc"]}
                if element.get("content_desc")
                else {"id": resource_id}
                if resource_id
                else value
            )
            try:
                resolved = self._find_element(locator)
                if not resolved.is_displayed() or not resolved.is_enabled():
                    continue
            except (LookupError, WebDriverException, ValueError):
                continue
            candidates.append(
                {
                    "locator": locator,
                    "confidence": confidence,
                    "method": "fuzzy_text_label",
                    "explanation": f"Matched Android UI label {value!r}.",
                }
            )
        return sorted(candidates, key=lambda item: item["confidence"], reverse=True)

    def healing_snapshot(self) -> str:
        """Return a password-redacted hierarchy for opt-in AI healing."""
        return _redact_sensitive_xml(self._require_driver().page_source)

    def validate_healing_locator(
        self,
        original: str | dict[str, str],
        replacement: dict[str, Any],
        action: str,
    ) -> bool:
        """Check Android replacement locator uniqueness, visibility, and type."""
        if not isinstance(replacement, dict) or len(replacement) != 1:
            return False
        strategy, value = next(iter(replacement.items()))
        if strategy not in {"id", "xpath", "accessibility_id"} or not isinstance(value, str):
            return False
        by = LOCATOR_KEYS[strategy]
        try:
            elements = self._require_driver().find_elements(by, value)
            if len(elements) != 1 or not elements[0].is_displayed() or not elements[0].is_enabled():
                return False
            if action in {"type", "select_option"}:
                return elements[0].tag_name.lower() in {
                    "android.widget.edittext",
                    "edittext",
                }
            if action == "click":
                clickable = elements[0].get_attribute("clickable")
                if clickable is not None and clickable.lower() != "true":
                    return False
            return True
        except (WebDriverException, ValueError):
            return False

    def swipe(self, direction: str) -> None:
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError(f"Unsupported swipe direction: {direction}")
        driver = self._require_driver()
        size = driver.get_window_size()
        width, height = size["width"], size["height"]
        driver.execute_script(
            "mobile: swipeGesture",
            {
                "left": 0,
                "top": 0,
                "width": width,
                "height": height,
                "direction": direction,
                "percent": 0.75,
            },
        )

    def scroll_to(self, text: str) -> None:
        escaped_text = json.dumps(text)
        selector = (
            "new UiScrollable(new UiSelector().scrollable(true))"
            f".scrollIntoView(new UiSelector().textContains({escaped_text}))"
        )
        self._wait_for_element(AppiumBy.ANDROID_UIAUTOMATOR, selector)

    def back(self) -> None:
        self._require_driver().back()

    def home(self) -> None:
        self._require_driver().press_keycode(3)

    def hide_keyboard(self) -> None:
        driver = self._require_driver()
        try:
            if driver.is_keyboard_shown():
                driver.hide_keyboard()
        except WebDriverException:
            driver.hide_keyboard()

    def press_key(self, key: str) -> None:
        keycodes = {"enter": 66, "back": 4, "home": 3}
        try:
            keycode = keycodes[key]
        except KeyError as exc:
            raise ValueError(f"Unsupported Android key: {key}") from exc
        self._require_driver().press_keycode(keycode)

    def set_orientation(self, orientation: str) -> None:
        normalized = orientation.upper()
        if normalized not in {"PORTRAIT", "LANDSCAPE"}:
            raise ValueError(f"Unsupported orientation: {orientation}")
        self._require_driver().orientation = normalized

    def close(self) -> None:
        """Stop recording/logcat and close the Appium session."""
        errors: list[Exception] = []
        if self._driver is not None:
            try:
                if self._recording_started:
                    recording = self._driver.stop_recording_screen()
                    if recording:
                        video_path = self.artifact_dir / "screen_recording.mp4"
                        video_path.write_bytes(base64.b64decode(recording))
            except Exception as exc:
                errors.append(exc)
            finally:
                self._recording_started = False
            try:
                self._driver.quit()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._driver = None
        try:
            self._stop_logcat()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError(f"Could not fully close Android session: {errors[0]}") from errors[0]

    def _select_device(self, manager: DeviceManager) -> tuple[str, dict[str, str] | None]:
        if self.settings.device != "auto":
            devices = manager.list_devices()
            selected = next(
                (item for item in devices if item["serial"] == self.settings.device),
                None,
            )
            if selected is None:
                raise RuntimeError(
                    f"Android device {self.settings.device!r} is not connected or authorized. Check `adb devices -l`."
                )
            return selected["serial"], selected
        devices = manager.list_devices()
        if not devices:
            raise RuntimeError(
                "No authorized Android device is connected. Start an emulator or "
                "connect a USB device, then run `adb devices -l`."
            )
        return devices[0]["serial"], devices[0]

    def _apk_path(self, destination: str) -> Path | None:
        candidate = self.settings.app or destination
        if not candidate or not candidate.lower().endswith(".apk"):
            return None
        path = Path(candidate).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Android APK was not found: {path}")
        return path

    def _start_logcat(self, adb_path: str | None) -> None:
        if not adb_path or not self._serial:
            raise RuntimeError("adb is required to capture Android logcat. Install SDK Platform-Tools.")
        self._logcat_file = (self.artifact_dir / "logcat.txt").open("w", encoding="utf-8")
        try:
            subprocess.run(
                [adb_path, "-s", self._serial, "logcat", "-c"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self._logcat_process = subprocess.Popen(
                [adb_path, "-s", self._serial, "logcat", "-v", "time"],
                stdout=self._logcat_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            self._logcat_file.close()
            self._logcat_file = None
            raise

    def _stop_logcat(self) -> None:
        if self._logcat_process is not None:
            if self._logcat_process.poll() is None:
                self._logcat_process.terminate()
                try:
                    self._logcat_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._logcat_process.kill()
                    self._logcat_process.wait(timeout=5)
            self._logcat_process = None
        if self._logcat_file is not None:
            self._logcat_file.close()
            self._logcat_file = None

    def _require_driver(self) -> AppiumWebDriver:
        if self._driver is None:
            raise RuntimeError("Start an Appium session before using the Android driver.")
        return self._driver

    def _find_element(self, target: str | dict[str, str]):
        if isinstance(target, dict):
            locator_name, selector = next(iter(target.items()))
            try:
                locator = LOCATOR_KEYS[locator_name]
            except KeyError as exc:
                raise ValueError(f"Unsupported Android locator: {locator_name}") from exc
            return self._wait_for_element(locator, selector)

        escaped = json.dumps(target)
        locators = (
            (AppiumBy.ACCESSIBILITY_ID, target),
            (AppiumBy.ANDROID_UIAUTOMATOR, f"new UiSelector().text({escaped})"),
            (
                AppiumBy.ANDROID_UIAUTOMATOR,
                f"new UiSelector().textContains({escaped})",
            ),
            (AppiumBy.ID, target),
            (
                AppiumBy.ANDROID_UIAUTOMATOR,
                f"new UiSelector().description({escaped})",
            ),
            (
                AppiumBy.XPATH,
                f"//*[@content-desc={_xpath_literal(target)} "
                f"or @text={_xpath_literal(target)} "
                f"or contains(@text, {_xpath_literal(target)})]",
            ),
        )
        last_error: Exception | None = None

        def find_first(driver: AppiumWebDriver) -> Any:
            nonlocal last_error
            for by, selector in locators:
                try:
                    return driver.find_element(by, selector)
                except (NoSuchElementException, InvalidSelectorException) as exc:
                    last_error = exc
            raise NoSuchElementException("No Android locator strategy matched.")

        try:
            return WebDriverWait(
                self._require_driver(),
                self.settings.timeouts.explicit,
                poll_frequency=min(0.2, self.settings.timeouts.explicit),
                ignored_exceptions=(InvalidSelectorException,),
            ).until(find_first)
        except TimeoutException as exc:
            last_error = last_error or exc
        raise LookupError(
            f"Could not find Android element matching {target!r} within {self.settings.timeouts.explicit:g}s."
        ) from last_error

    def _wait_for_element(self, by: str, selector: str):
        timeout = self.settings.timeouts.explicit
        try:
            return WebDriverWait(
                self._require_driver(),
                timeout,
                poll_frequency=min(0.2, timeout),
                ignored_exceptions=(InvalidSelectorException,),
            ).until(lambda driver: driver.find_element(by, selector))
        except TimeoutException as exc:
            raise LookupError(f"Could not find Android element by {by}={selector!r} within {timeout:g}s.") from exc


def parse_page_source(source: str) -> list[dict[str, Any]]:
    """Parse Android UI hierarchy XML into inspector-friendly element records."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise ValueError(f"Could not parse Android page source XML: {exc}") from exc
    elements = []
    for node in root.iter():
        attributes = node.attrib
        if not any(attributes.get(key) for key in ("text", "resource-id", "content-desc", "clickable")):
            continue
        element = {
            "text": attributes.get("text", ""),
            "id": attributes.get("resource-id", ""),
            "content_desc": attributes.get("content-desc", ""),
            "class": attributes.get("class", ""),
            "bounds": attributes.get("bounds", ""),
            "clickable": attributes.get("clickable", "false").lower() == "true",
        }
        if attributes.get("password", "false").lower() == "true":
            element["password"] = True
        elements.append(element)
    return elements


def _redact_sensitive_xml(source: str) -> str:
    """Redact field contents from diagnostic XML before writing it to disk."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise ValueError(f"Could not redact Android page source XML: {exc}") from exc
    sensitive_words = ("password", "token", "secret", "otp", "cvv", "card")
    for node in root.iter():
        attributes = node.attrib
        descriptor = " ".join(
            (
                attributes.get("resource-id", ""),
                attributes.get("content-desc", ""),
                attributes.get("class", ""),
            )
        ).lower()
        if attributes.get("password", "").lower() == "true" or any(word in descriptor for word in sensitive_words):
            attributes["text"] = ""
            attributes["content-desc"] = ""
    return ET.tostring(root, encoding="unicode")


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def _mobile_target_text(target: str | dict[str, str]) -> str:
    if isinstance(target, str):
        return target
    if isinstance(target, dict) and len(target) == 1:
        return str(next(iter(target.values())))
    return ""

"""Android device and emulator discovery through the Android SDK tools."""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class DeviceManager:
    """Discover connected Android devices and manage local AVDs."""

    def __init__(
        self,
        *,
        adb_path: str | None = None,
        emulator_path: str | None = None,
        popen: Any = subprocess.Popen,
        run: Any = subprocess.run,
    ) -> None:
        self.adb_path = adb_path or _find_sdk_tool("adb")
        self.emulator_path = emulator_path or _find_sdk_tool("emulator")
        self._popen = popen
        self._run = run

    def list_devices(self) -> list[dict[str, str]]:
        """List authorized USB/emulator devices and their Android metadata."""
        if not self.adb_path:
            raise RuntimeError(
                "adb was not found. Install Android SDK Platform-Tools and add "
                "platform-tools to PATH or set ANDROID_HOME."
            )
        try:
            output = self._run(
                [self.adb_path, "devices", "-l"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"adb devices failed: {exc}") from exc
        devices = []
        for line in output.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2 or fields[1] != "device":
                continue
            serial = fields[0]
            details = _parse_device_details(fields[2:])
            model = details.get("model", "").replace("_", " ")
            devices.append(
                {
                    "serial": serial,
                    "model": model or self._getprop(serial, "ro.product.model"),
                    "android_version": self._getprop(serial, "ro.build.version.release"),
                    "status": "device",
                    "type": "emulator" if serial.startswith("emulator-") else "usb",
                }
            )
        return devices

    def list_avds(self) -> list[str]:
        """Return names of locally configured Android virtual devices."""
        if not self.emulator_path:
            raise RuntimeError(
                "The Android emulator binary was not found. Install it from Android "
                "Studio SDK Manager and add the emulator directory to PATH."
            )
        try:
            output = self._run(
                [self.emulator_path, "-list-avds"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"emulator -list-avds failed: {exc}") from exc
        return [line.strip() for line in output.splitlines() if line.strip()]

    def touch_axis_ranges(self, serial: str) -> tuple[int, int, int, int] | None:
        """Return raw minimum/maximum coordinates reported by the touch device."""
        if not self.adb_path:
            return None
        try:
            output = self._run(
                [self.adb_path, "-s", serial, "shell", "getevent", "-lp"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not inspect Android touch ranges: {exc}") from exc
        x_range = _parse_touch_range(output, "ABS_MT_POSITION_X")
        y_range = _parse_touch_range(output, "ABS_MT_POSITION_Y")
        if x_range is None or y_range is None:
            return None
        return x_range[0], x_range[1], y_range[0], y_range[1]

    def start_emulator(
        self,
        avd_name: str,
        *,
        timeout_seconds: float = 180,
        poll_seconds: float = 2,
    ) -> str:
        """Launch an AVD and return its serial after Android reports boot complete."""
        if not avd_name.strip():
            raise ValueError("AVD name must not be empty.")
        if not self.emulator_path:
            raise RuntimeError("The Android emulator binary was not found on PATH.")
        avds = self.list_avds()
        if avd_name not in avds:
            raise ValueError(f"AVD {avd_name!r} was not found. Available AVDs: {', '.join(avds) or '(none)'}.")

        self._popen(
            [self.emulator_path, "-avd", avd_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **_background_process_options(),
        )
        deadline = time.monotonic() + timeout_seconds
        last_serial: str | None = None
        while time.monotonic() < deadline:
            for device in self.list_devices():
                if device["type"] == "emulator":
                    last_serial = device["serial"]
                    if self._getprop(last_serial, "sys.boot_completed") == "1":
                        return last_serial
            time.sleep(poll_seconds)
        raise TimeoutError(
            f"Emulator {avd_name!r} did not finish booting within "
            f"{timeout_seconds:g}s" + (f" (last serial: {last_serial})." if last_serial else ".")
        )

    def _getprop(self, serial: str, prop: str) -> str:
        if not self.adb_path:
            return ""
        try:
            result = self._run(
                [self.adb_path, "-s", serial, "shell", "getprop", prop],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.SubprocessError:
            return ""
        return result.stdout.strip()


def _find_sdk_tool(name: str) -> str | None:
    """Find an Android SDK utility in PATH or standard SDK locations."""
    executable = f"{name}.exe" if os.name == "nt" else name
    on_path = shutil.which(executable) or shutil.which(name)
    if on_path:
        return on_path
    roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "AppData" / "Local" / "Android" / "Sdk"),
        str(Path.home() / "Library" / "Android" / "sdk"),
        str(Path.home() / "Android" / "Sdk"),
    ]
    subdirectory = "platform-tools" if name == "adb" else "emulator"
    for root in roots:
        if root:
            candidate = Path(root) / subdirectory / executable
            if candidate.is_file():
                return str(candidate)
    return None


def _parse_device_details(fields: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        match = re.match(r"([^:]+):(.+)", field)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def _parse_touch_range(output: str, axis: str) -> tuple[int, int] | None:
    match = re.search(
        rf"{axis}[^\r\n]*?\bmin\s+(\d+)[^\r\n]*?\bmax\s+(\d+)",
        output,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    minimum, maximum = map(int, match.groups())
    return (minimum, maximum) if maximum > minimum else None


def _background_process_options() -> dict[str, Any]:
    if os.name == "nt":
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        }
    return {"start_new_session": True}

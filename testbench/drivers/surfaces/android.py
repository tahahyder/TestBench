"""ADB-backed surface for an Android game without starting an Appium session."""

import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from testbench.drivers.devices import DeviceManager
from testbench.drivers.surfaces.base import Surface


class AndroidSurface(Surface):
    """Capture an Android screen and send direct adb input events."""

    def __init__(self, device: str, artifact_dir: Path) -> None:
        self.manager = DeviceManager()
        self.device = device
        self.artifact_dir = artifact_dir
        self.adb_path = self.manager.adb_path
        self.serial: str | None = None
        self._recording_path = "/sdcard/testbench_game_recording.mp4"
        self._recording_pid: int | None = None

    def open(self) -> None:
        if not self.adb_path:
            raise RuntimeError(
                "adb was not found. Install Android SDK Platform-Tools, set "
                "ANDROID_HOME, and add platform-tools to PATH."
            )
        devices = self.manager.list_devices()
        selected = (
            next((item for item in devices if item["serial"] == self.device), None)
            if self.device != "auto"
            else next(iter(devices), None)
        )
        if selected is None:
            raise RuntimeError(
                f"Android game device {self.device!r} is not connected and authorized. Check `adb devices -l`."
            )
        self.serial = selected["serial"]
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        output = self._adb_bytes(
            "shell",
            "sh",
            "-c",
            f"screenrecord {self._recording_path} >/dev/null 2>&1 & echo $!",
        )
        try:
            self._recording_pid = int(output.decode(errors="replace").strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                "Could not start Android screen recording; adb did not return the screenrecord process ID."
            ) from exc

    def capture(self) -> np.ndarray:
        data = self._adb_bytes("exec-out", "screencap", "-p")
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError("adb screencap returned an unreadable image.")
        return image

    def click(self, x: int, y: int) -> None:
        self._adb("shell", "input", "tap", str(x), str(y))

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        self._adb(
            "shell",
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(max(1, round(duration * 1000))),
        )

    def key_down(self, key: str) -> None:
        self._adb("shell", "input", "keyevent", _android_key(key))

    def key_up(self, key: str) -> None:
        self._adb("shell", "input", "keyevent", "--longpress", _android_key(key))

    def press(self, key: str) -> None:
        self._adb("shell", "input", "keyevent", _android_key(key))

    def type_text(self, text: str) -> None:
        safe = text.replace("%", "%25").replace(" ", "%s").replace("&", "\\&")
        self._adb("shell", "input", "text", safe)

    def size(self) -> tuple[int, int]:
        image = self.capture()
        return image.shape[1], image.shape[0]

    def close(self) -> None:
        if self._recording_pid is None:
            return
        if not self.adb_path or not self.serial:
            raise RuntimeError("Cannot stop Android recording without its adb device.")
        pid = self._recording_pid
        is_running = self._remote_process_running(pid)
        if is_running:
            try:
                self._adb("shell", "kill", "-2", str(pid))
            except RuntimeError:
                if self._remote_process_running(pid):
                    raise
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and self._remote_process_running(pid):
                time.sleep(0.1)
            if self._remote_process_running(pid):
                self._adb("shell", "kill", "-9", str(pid))
                raise RuntimeError("Android screenrecord did not stop after SIGINT.")

        remote = self._recording_path
        local = self.artifact_dir / "screen_recording.mp4"
        try:
            subprocess.run(
                [self.adb_path, "-s", self.serial, "pull", remote, str(local)],
                check=True,
                capture_output=True,
                timeout=15,
            )
            subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "rm", "-f", remote],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not retrieve Android game recording: {exc}") from exc
        self._recording_pid = None

    def _remote_process_running(self, pid: int) -> bool:
        if not self.adb_path or not self.serial:
            raise RuntimeError("Open the Android game surface before checking recording.")
        try:
            result = subprocess.run(
                [
                    self.adb_path,
                    "-s",
                    self.serial,
                    "shell",
                    "kill",
                    "-0",
                    str(pid),
                ],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not check Android screenrecord: {exc}") from exc
        return result.returncode == 0

    def _adb(self, *arguments: str) -> None:
        self._adb_bytes(*arguments)

    def _adb_bytes(self, *arguments: str) -> bytes:
        if not self.adb_path or not self.serial:
            raise RuntimeError("Open the Android game surface before sending adb input.")
        try:
            return subprocess.run(
                [self.adb_path, "-s", self.serial, *arguments],
                check=True,
                capture_output=True,
                timeout=20,
            ).stdout
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode(errors="replace") if exc.stderr else str(exc)
            raise RuntimeError(f"adb command failed: {detail}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not run adb command: {exc}") from exc


def _android_key(key: str) -> str:
    aliases = {
        "space": "62",
        "enter": "66",
        "back": "4",
        "home": "3",
        "left": "21",
        "right": "22",
        "up": "19",
        "down": "20",
        "escape": "111",
    }
    return aliases.get(key.lower(), key)

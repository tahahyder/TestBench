import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from testbench.drivers.surfaces.android import AndroidSurface
from testbench.drivers.surfaces.fake import FakeSurface


def test_fake_surface_returns_copied_frames_and_tracks_input() -> None:
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    surface = FakeSurface([frame])

    captured = surface.capture()
    captured[0, 0] = 255
    surface.click(2, 3)
    surface.drag(1, 2, 3, 4, 0.1)
    surface.press("space")

    assert np.all(frame == 0)
    assert surface.size() == (30, 20)
    assert surface.actions == [
        ("click", 2, 3),
        ("drag", 1, 2, 3, 4, 0.1),
        ("press", "space"),
    ]


def test_android_surface_uses_adb_and_finishes_recording(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeDeviceManager:
        adb_path = "adb"

        def list_devices(self):
            return [{"serial": "emulator-5554"}]

    monkeypatch.setattr(
        "testbench.drivers.surfaces.android.DeviceManager",
        FakeDeviceManager,
    )
    surface = AndroidSurface("auto", tmp_path)
    commands = []
    monkeypatch.setattr(
        surface,
        "_adb_bytes",
        lambda *args: commands.append(args) or b"12345\n",
    )
    running_results = iter([True, False, False])
    monkeypatch.setattr(
        surface,
        "_remote_process_running",
        lambda pid: next(running_results),
    )
    subprocess_commands = []

    def fake_run(command, **kwargs):
        subprocess_commands.append(command)
        if command[3] == "pull":
            Path(command[-1]).write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    surface.open()
    surface.close()

    assert commands == [
        (
            "shell",
            "sh",
            "-c",
            "screenrecord /sdcard/testbench_game_recording.mp4 >/dev/null 2>&1 & echo $!",
        ),
        ("shell", "kill", "-2", "12345"),
    ]
    assert subprocess_commands[0][3] == "pull"
    assert subprocess_commands[1][3:7] == [
        "shell",
        "rm",
        "-f",
        "/sdcard/testbench_game_recording.mp4",
    ]
    assert (tmp_path / "screen_recording.mp4").read_bytes() == b"mp4"

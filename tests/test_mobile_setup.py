import subprocess
import sys
from pathlib import Path

from testbench.setup import mobile_setup


def test_install_runs_npm_and_uiautomator2_commands(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(mobile_setup.shutil, "which", lambda name: f"C:/tools/{name}.cmd")
    monkeypatch.setattr(mobile_setup, "_run_checked", lambda command: calls.append(command))

    mobile_setup.install_appium()

    assert calls == [
        mobile_setup._cli_command("C:/tools/npm.cmd", "install", "--global", "appium"),
        mobile_setup._cli_command("C:/tools/appium.cmd", "driver", "install", "uiautomator2"),
    ]


def test_start_appium_waits_for_status_and_saves_pid(tmp_path: Path, monkeypatch) -> None:
    states = iter([False, True])

    class FakeProcess:
        pid = 12345

        def poll(self):
            return None

    monkeypatch.setattr(mobile_setup.shutil, "which", lambda _: "appium")
    monkeypatch.setattr(mobile_setup, "appium_healthy", lambda _: next(states))
    monkeypatch.setattr(mobile_setup.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(mobile_setup.time, "sleep", lambda _: None)
    pid_file = tmp_path / "appium.pid"

    pid = mobile_setup.start_appium(
        pid_file=pid_file,
        log_file=tmp_path / "appium.log",
    )

    assert pid == 12345
    assert pid_file.read_text(encoding="ascii") == "12345"


def test_stop_appium_stops_only_managed_process(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "appium.pid"
    pid_file.write_text("6789", encoding="ascii")
    stopped = []
    monkeypatch.setattr(mobile_setup, "appium_healthy", lambda _: True)
    monkeypatch.setattr(mobile_setup, "_stop_pid", stopped.append)

    assert mobile_setup.stop_appium(pid_file=pid_file)
    assert stopped == [6789]
    assert not pid_file.exists()


def test_managed_orphan_process_is_terminated():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        mobile_setup._stop_pid(process.pid)
        process.wait(timeout=5)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

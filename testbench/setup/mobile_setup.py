"""Install and manage a local Appium 2 server."""

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SERVER_URL = "http://127.0.0.1:4723"
PID_FILE = Path("runs") / "appium.pid"
LOG_FILE = Path("runs") / "appium-server.log"


def install_appium() -> None:
    """Install Appium globally with npm and its official Android driver."""
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm was not found. Install Node.js LTS before Appium.")
    _run_checked(_cli_command(npm, "install", "--global", "appium"))
    appium = shutil.which("appium")
    if not appium:
        raise RuntimeError(
            "npm installed Appium but the appium command is not on PATH. Restart the terminal and retry."
        )
    _run_checked(_cli_command(appium, "driver", "install", "uiautomator2"))


def start_appium(
    *,
    host: str = "127.0.0.1",
    port: int = 4723,
    timeout_seconds: float = 30,
    pid_file: Path = PID_FILE,
    log_file: Path = LOG_FILE,
) -> int:
    """Start Appium detached, wait for its status endpoint, and return its PID."""
    base_url = f"http://{host}:{port}"
    if appium_healthy(base_url):
        raise RuntimeError(f"Appium is already responding at {base_url}.")
    executable = shutil.which("appium")
    if not executable:
        raise RuntimeError("Appium is not installed. Run: testbench mobile install")

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as log:
        command = _server_command(executable, host, port)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **_background_options(),
        )
    pid_file.write_text(str(process.pid), encoding="ascii")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if appium_healthy(base_url):
            return process.pid
        if process.poll() is not None:
            pid_file.unlink(missing_ok=True)
            raise RuntimeError(f"Appium exited during startup. Check {log_file.resolve()} for details.")
        time.sleep(0.5)

    _stop_pid(process.pid)
    pid_file.unlink(missing_ok=True)
    raise TimeoutError(
        f"Appium did not become healthy at {base_url} within {timeout_seconds:g}s. Check {log_file.resolve()}."
    )


def stop_appium(
    *,
    pid_file: Path = PID_FILE,
    server_url: str = DEFAULT_SERVER_URL,
) -> bool:
    """Stop the managed Appium process; return whether a server was stopped."""
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
        except ValueError as exc:
            raise RuntimeError(f"Invalid Appium PID in {pid_file}.") from exc
        _stop_pid(pid)
        pid_file.unlink(missing_ok=True)
        return True
    if appium_healthy(server_url):
        raise RuntimeError(
            f"Appium is responding at {server_url}, but no TestBench-managed "
            "process ID was found; stop it from its own terminal."
        )
    return False


def appium_healthy(server_url: str = DEFAULT_SERVER_URL) -> bool:
    """Return whether an Appium 2 status endpoint is healthy."""
    try:
        request = urllib.request.Request(
            server_url.rstrip("/") + "/status",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError):
        return False
    value = payload.get("value", {})
    return response.status == 200 and (value.get("ready") is True or payload.get("status") == 0)


def _run_checked(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, check=True)


def _server_command(executable: str, host: str, port: int) -> list[str]:
    arguments = ["server", "--address", host, "--port", str(port)]
    return _cli_command(executable, *arguments)


def _cli_command(executable: str, *arguments: str) -> list[str]:
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", executable, *arguments]
    return [executable, *arguments]


def _background_options() -> dict:
    if os.name == "nt":
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        }
    return {"start_new_session": True}


def _stop_pid(pid: int) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode and "not found" not in result.stderr.lower():
            raise RuntimeError(f"Could not stop Appium PID {pid}: {result.stderr.strip()}")
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

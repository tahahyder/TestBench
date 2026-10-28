"""Check local dependencies and print actionable installation commands."""

import contextlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run_doctor(*, mobile: bool = False, json_output: bool = False) -> int:
    """Report required and optional dependencies; return nonzero if web is unusable."""
    if json_output:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = run_doctor(mobile=mobile)
        checks: list[dict[str, str | bool]] = []
        pending: dict[str, str | bool] | None = None
        for line in output.getvalue().splitlines():
            if line.startswith(("[OK] ", "[MISSING] ")):
                if pending is not None:
                    checks.append(pending)
                available = line.startswith("[OK] ")
                prefix_length = len("[OK] ") if available else len("[MISSING] ")
                label, _, detail = line[prefix_length:].partition(": ")
                pending = {
                    "name": label,
                    "ok": available,
                    "detail": detail,
                    "fix": "",
                }
            elif line.strip().startswith("Fix:") and pending is not None:
                pending["fix"] = line.strip()[5:].strip()
        if pending is not None:
            checks.append(pending)
        print(
            json.dumps(
                {"ok": status == 0, "checks": checks},
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
        return status

    print("TestBench environment doctor\n")
    python_ok = sys.version_info >= (3, 11)
    _print_check(
        "Python 3.11+",
        python_ok,
        f"Detected Python {sys.version.split()[0]}",
        "Install Python 3.11+ from https://www.python.org/downloads/ and rerun with it.",
    )
    if not python_ok:
        print("  Fix: winget install Python.Python.3.12")

    playwright_ok = _playwright_browser_available()
    _print_check(
        "Playwright Chromium",
        playwright_ok,
        "Chromium browser is installed",
        "Run: python -m playwright install chromium",
    )

    node_path = shutil.which("node")
    node_version = _command_output(_executable_command(node_path, "--version")) if node_path else ""
    _print_check(
        "Node.js (for Appium)",
        node_path is not None,
        f"{node_version.strip()} at {node_path}" if node_path else "Node.js was not found on PATH",
        _node_install_instructions(),
    )
    java_info, java_ok = _java_jdk17_status()
    _print_check(
        "Java JDK 17+",
        java_ok,
        java_info if java_ok else "JDK 17+ was not found",
        _java_install_instructions(),
    )

    sdk_path = os.environ.get("ANDROID_HOME")
    sdk_available = bool(sdk_path and Path(sdk_path).is_dir())
    _print_check(
        "Android SDK",
        sdk_available,
        sdk_path if sdk_available else "ANDROID_HOME is not configured to an existing SDK directory",
        _android_sdk_install_instructions(),
    )
    sdk_adb = Path(sdk_path) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb") if sdk_available else None
    adb_path = shutil.which("adb") or (str(sdk_adb) if sdk_adb and sdk_adb.is_file() else None)
    adb_version = _command_output([adb_path, "version"]) if adb_path else ""
    _print_check(
        "adb",
        adb_path is not None,
        f"{adb_version.splitlines()[0]} at {adb_path}"
        if adb_path and adb_version
        else (adb_path or "adb was not found"),
        "Install Android SDK Platform-Tools from Android Studio SDK Manager and add "
        "ANDROID_HOME/platform-tools to PATH.",
    )
    sdk_emulator = (
        Path(sdk_path) / "emulator" / ("emulator.exe" if os.name == "nt" else "emulator") if sdk_available else None
    )
    emulator_path = shutil.which("emulator") or (str(sdk_emulator) if sdk_emulator and sdk_emulator.is_file() else None)
    _print_check(
        "Android emulator",
        emulator_path is not None,
        emulator_path or "emulator binary was not found",
        "Install Android Emulator from Android Studio SDK Manager and add ANDROID_HOME/emulator to PATH.",
    )

    appium_path = shutil.which("appium")
    appium_version = _command_output(_executable_command(appium_path, "--version")) if appium_path else ""
    appium_major = _leading_version(appium_version)
    appium_ok = bool(appium_path and appium_major and appium_major >= 2)
    _print_check(
        "Appium 2.x",
        appium_ok,
        f"{appium_version.strip()} at {appium_path}" if appium_ok else "Appium 2.x was not found",
        "Run: npm install --global appium; then: appium driver install uiautomator2",
    )
    driver_output = (
        _command_output(_executable_command(appium_path, "driver", "list", "--installed"))
        if appium_ok and appium_path
        else ""
    )
    uiautomator_installed = "uiautomator2" in driver_output.lower()
    _print_check(
        "Appium UiAutomator2 driver",
        uiautomator_installed,
        "uiautomator2 is installed" if uiautomator_installed else "uiautomator2 is not installed",
        "Run: appium driver install uiautomator2",
    )

    missing_python_packages = []
    for package in (
        "fastapi",
        "playwright",
        "pydantic",
        "PyYAML",
        "SQLAlchemy",
        "typer",
    ):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing_python_packages.append(package)
        else:
            print(f"[OK] {package} {version}")
    if missing_python_packages:
        print("[MISSING] Python packages: " + ", ".join(missing_python_packages))
        print('  Fix: python -m pip install "testbench"')

    optional_packages = {
        "Android Appium client": ("appium-python-client", "mobile"),
        "Game vision engine": ("opencv-python-headless", "game"),
        "Game desktop capture": ("mss", "game"),
        "Game input": ("pyautogui", "game"),
        "Game OCR wrapper": ("pytesseract", "game"),
        "AI fake data": ("faker", "ai"),
    }
    for label, (distribution, extra) in optional_packages.items():
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            _print_check(
                label,
                False,
                "optional dependency is not installed",
                f'Run: python -m pip install "testbench[{extra}]"',
            )
        else:
            _print_check(label, True, f"{distribution} {version}", "")
    tesseract_path = shutil.which("tesseract")
    _print_check(
        "Tesseract OCR engine",
        tesseract_path is not None,
        tesseract_path or "optional executable not found",
        _tesseract_install_instructions(),
    )

    try:
        from testbench.ai.config import load_ai_config

        ai_config = load_ai_config()
    except (OSError, ValueError) as exc:
        _print_check("AI configuration", False, str(exc), "Fix testbench.toml and rerun `testbench doctor`.")
    else:
        if not ai_config.get("enabled"):
            _print_check("AI assist", True, "disabled by default; no provider contacted", "")
        elif ai_config.get("provider") not in ai_config.get("consented_providers", []):
            _print_check(
                "AI provider",
                False,
                "AI is enabled but consent has not been recorded",
                f"Run: testbench ai consent {ai_config.get('provider', 'ollama')}",
            )
        else:
            try:
                from testbench.ai.providers import create_provider

                provider = create_provider(ai_config)
                healthy, detail = provider.health(str(ai_config.get("model", "")))
            except (OSError, RuntimeError, ValueError) as exc:
                healthy, detail = False, str(exc)
            _print_check(
                "AI provider",
                healthy,
                detail,
                "Run `testbench ai doctor` for provider-specific setup guidance.",
            )

    if python_ok and playwright_ok:
        print("\nSummary: web testing is ready.")
    else:
        print("\nSummary: web testing needs setup; use the fixes above.")
    print("AI provider reachability: run `testbench ai doctor` (no prompts are sent by this check).")
    print("\nWeb tests require Python 3.11+ and Playwright Chromium.")
    print(
        "Android tests additionally require Node.js, JDK 17+, Android SDK, adb, emulator, Appium 2.x, and UiAutomator2."
    )
    if mobile:
        return int(
            not all(
                (
                    python_ok,
                    node_path is not None,
                    java_ok,
                    sdk_available,
                    adb_path is not None,
                    emulator_path is not None,
                    appium_ok,
                    uiautomator_installed,
                )
            )
        )
    return 0 if python_ok and playwright_ok else 1


def _command_output(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout + result.stderr


def _executable_command(executable: str, *arguments: str) -> list[str]:
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", executable, *arguments]
    return [executable, *arguments]


def _leading_version(value: str) -> int | None:
    match = re.search(r"(\d+)(?:\.\d+)*", value)
    return int(match.group(1)) if match else None


def _check_java_jdk17() -> bool:
    return _java_jdk17_status()[1]


def _java_jdk17_status() -> tuple[str, bool]:
    java = shutil.which("java")
    javac = shutil.which("javac")
    if not java or not javac:
        return "java or javac was not found on PATH", False
    version = _command_output([java, "-version"])
    compiler = _command_output([javac, "-version"])
    java_major = _java_major(version) or 0
    compiler_major = _java_major(compiler) or 0
    details = f"java {java_major} at {java}; javac {compiler_major} at {javac}"
    return details, java_major >= 17 and compiler_major >= 17


def _java_major(value: str) -> int | None:
    match = re.search(r'version\s+"?(\d+)|javac\s+(\d+)', value)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _java_install_instructions() -> str:
    if sys.platform == "win32":
        return "Run: winget install EclipseAdoptium.Temurin.17.JDK; reopen the terminal."
    if sys.platform == "darwin":
        return "Run: brew install --cask temurin@17; then set JAVA_HOME=$(/usr/libexec/java_home -v 17)."
    return "Run: sudo apt update && sudo apt install -y openjdk-17-jdk (Debian/Ubuntu); set JAVA_HOME."


def _android_sdk_install_instructions() -> str:
    if sys.platform == "win32":
        return 'Install Android Studio: winget install Google.AndroidStudio; setx ANDROID_HOME "$env:LOCALAPPDATA\\Android\\Sdk".'
    if sys.platform == "darwin":
        return 'Install Android Studio: brew install --cask android-studio; export ANDROID_HOME="$HOME/Library/Android/sdk".'
    return 'Install Android Studio from https://developer.android.com/studio; export ANDROID_HOME="$HOME/Android/Sdk".'


def _node_install_instructions() -> str:
    if sys.platform == "win32":
        return "Run: winget install OpenJS.NodeJS.LTS; reopen the terminal."
    if sys.platform == "darwin":
        return "Run: brew install node."
    return "Run: sudo apt update && sudo apt install -y nodejs npm (Debian/Ubuntu)."


def _tesseract_install_instructions() -> str:
    if sys.platform == "win32":
        return "Run: winget install UB-Mannheim.TesseractOCR; reopen the terminal."
    if sys.platform == "darwin":
        return "Run: brew install tesseract."
    return "Run: sudo apt update && sudo apt install -y tesseract-ocr (Debian/Ubuntu)."


def _playwright_browser_available() -> bool:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override == "0":
        try:
            import playwright
        except ImportError:
            return False
        browser_cache = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    elif override:
        browser_cache = Path(override).expanduser()
    elif sys.platform == "win32":
        browser_cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ms-playwright"
    elif sys.platform == "darwin":
        browser_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        browser_cache = Path.home() / ".cache" / "ms-playwright"

    executables = (
        Path("chrome-win") / "chrome.exe",
        Path("chrome-win64") / "chrome.exe",
        Path("chrome-linux") / "chrome",
        Path("chrome-linux64") / "chrome",
        Path("chrome-mac") / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
        Path("chrome-win") / "chrome-headless-shell.exe",
        Path("chrome-linux") / "chrome-headless-shell",
    )
    return any(
        (installation / executable).is_file()
        for pattern in ("chromium-*", "chromium_headless_shell-*")
        for installation in browser_cache.glob(pattern)
        for executable in executables
    )


def _print_check(label: str, available: bool, detail: str, fix: str) -> None:
    marker = "[OK]" if available else "[MISSING]"
    if sys.stdout.isatty():
        marker = f"\x1b[32m{marker}\x1b[0m" if available else f"\x1b[33m{marker}\x1b[0m"
    print(f"{marker} {label}: {detail}")
    if not available:
        print(f"  Fix: {fix}")

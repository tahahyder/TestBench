from pathlib import Path

from testbench.setup import doctor


def test_doctor_checks_playwright_browser_cache(tmp_path: Path, monkeypatch) -> None:
    browser_dir = tmp_path / "chromium-1234" / "chrome-win64"
    browser_dir.mkdir(parents=True)
    (browser_dir / "chrome.exe").write_bytes(b"")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    assert doctor._playwright_browser_available()


def test_doctor_prints_missing_android_fixes(capsys, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_playwright_browser_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)

    assert doctor.run_doctor() == 0
    output = capsys.readouterr().out
    assert "winget install Google.AndroidStudio" in output
    assert "npm install --global appium" in output


def test_doctor_mobile_includes_sdk_and_appium_readiness(capsys, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_playwright_browser_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)

    assert doctor.run_doctor(mobile=True) == 1
    output = capsys.readouterr().out
    assert "Android SDK" in output
    assert "Android emulator" in output
    assert "Appium 2.x" in output
    assert "UiAutomator2" in output
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    assert "brew install --cask android-studio" in doctor._android_sdk_install_instructions()

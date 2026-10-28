from pathlib import Path

import pytest

from testbench.core.runner import run_test
from testbench.drivers.devices import DeviceManager
from testbench.setup.mobile_setup import appium_healthy


@pytest.mark.mobile
def test_android_settings_example_runs_when_device_and_appium_are_available() -> None:
    if not appium_healthy():
        pytest.skip("Appium is not running on http://127.0.0.1:4723.")
    try:
        if not DeviceManager().list_devices():
            pytest.skip("No authorized Android device or emulator is connected.")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Android SDK tools are unavailable: {exc}")

    result = run_test(Path(__file__).parents[1] / "tests_examples" / "android_example.yaml")

    assert result.status == "passed", result.steps

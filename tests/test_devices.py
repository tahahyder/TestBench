from types import SimpleNamespace

import pytest

from testbench.drivers.devices import DeviceManager, _parse_device_details


def test_parse_adb_device_details() -> None:
    assert _parse_device_details(["product:sdk_gphone64_x86_64", "model:Pixel_8", "transport_id:1"]) == {
        "product": "sdk_gphone64_x86_64",
        "model": "Pixel_8",
        "transport_id": "1",
    }


def test_list_devices_parses_connected_emulator_and_usb() -> None:
    def run(command, **kwargs):
        if command[1:3] == ["devices", "-l"]:
            return SimpleNamespace(
                stdout=(
                    "List of devices attached\n"
                    "emulator-5554 device product:sdk model:Pixel_8 transport_id:1\n"
                    "R58N123 device product:phone model:Galaxy_S23 transport_id:2\n"
                    "offline-1 offline\n"
                )
            )
        prop = command[-1]
        serial = command[2]
        value = "14" if prop.endswith(".release") else ("Pixel 8" if serial.startswith("emulator") else "Galaxy S23")
        return SimpleNamespace(stdout=value + "\n")

    manager = DeviceManager(adb_path="adb", emulator_path="emulator", run=run)

    assert manager.list_devices() == [
        {
            "serial": "emulator-5554",
            "model": "Pixel 8",
            "android_version": "14",
            "status": "device",
            "type": "emulator",
        },
        {
            "serial": "R58N123",
            "model": "Galaxy S23",
            "android_version": "14",
            "status": "device",
            "type": "usb",
        },
    ]


def test_list_avds_reads_emulator_output() -> None:
    manager = DeviceManager(
        adb_path="adb",
        emulator_path="emulator",
        run=lambda *args, **kwargs: SimpleNamespace(stdout="Pixel_8_API_35\n\nTablet_API_35\n"),
    )

    assert manager.list_avds() == ["Pixel_8_API_35", "Tablet_API_35"]


def test_adb_failure_has_actionable_runtime_error() -> None:
    import subprocess

    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="daemon unavailable")

    manager = DeviceManager(adb_path="adb", emulator_path="emulator", run=run)

    with pytest.raises(RuntimeError, match="adb devices failed"):
        manager.list_devices()


def test_start_emulator_rejects_unknown_avd() -> None:
    manager = DeviceManager(
        adb_path="adb",
        emulator_path="emulator",
        run=lambda *args, **kwargs: SimpleNamespace(stdout="known_avd\n"),
        popen=lambda *args, **kwargs: pytest.fail("must not start an unknown AVD"),
    )

    with pytest.raises(ValueError, match="Available AVDs: known_avd"):
        manager.start_emulator("missing")

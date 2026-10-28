import sys
from pathlib import Path
from types import ModuleType

import yaml

from testbench.core.runner import _create_driver, run_test
from testbench.drivers.base import Driver


class FakeDriver(Driver):
    def __init__(self, config: dict, artifact_dir: Path, *, fail_on: str = "") -> None:
        super().__init__(config, artifact_dir)
        self.fail_on = fail_on
        self.actions: list[str] = []

    def open(self, destination: str) -> None:
        self.actions.append(f"open:{destination}")

    def click(self, target: str) -> None:
        self.actions.append(f"click:{target}")
        if target == self.fail_on:
            raise AssertionError("element was not found")

    def type(self, field: str, value: str) -> None:
        self.actions.append(f"type:{field}:{value}")

    def wait(self, seconds: float) -> None:
        self.actions.append(f"wait:{seconds}")

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake screenshot")

    def expect_visible(self, target: str) -> None:
        self.actions.append(f"visible:{target}")

    def expect_text(self, text: str) -> None:
        self.actions.append(f"text:{text}")

    def close(self) -> None:
        self.actions.append("close")

    def healing_candidates(self, target):
        if target == "Old":
            return [
                {
                    "locator": "New",
                    "confidence": 0.9,
                    "method": "fuzzy_text_label",
                    "explanation": "Similar visible text",
                }
            ]
        return []

    def swipe(self, direction: str) -> None:
        self.actions.append(f"swipe:{direction}")

    def scroll_to(self, text: str) -> None:
        self.actions.append(f"scroll_to:{text}")

    def back(self) -> None:
        self.actions.append("back")

    def home(self) -> None:
        self.actions.append("home")

    def hide_keyboard(self) -> None:
        self.actions.append("hide_keyboard")

    def press_key(self, key: str) -> None:
        self.actions.append(f"press_key:{key}")

    def set_orientation(self, orientation: str) -> None:
        self.actions.append(f"orientation:{orientation}")


def write_test(path: Path, steps: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump(
            {"name": "sample", "target": "web", "steps": steps},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_runner_executes_steps_and_closes_driver(tmp_path: Path) -> None:
    test_file = tmp_path / "test.yaml"
    write_test(
        test_file,
        [
            {"open": "https://example.com"},
            {"type": {"field": "Email", "value": "a@b.com"}},
            {"click": "Login"},
            {"expect_text": "Dashboard"},
            {"screenshot": "after_login"},
        ],
    )
    driver = FakeDriver({}, tmp_path / "run")
    updates = []

    result = run_test(
        test_file,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        driver=driver,
        on_progress=lambda update: updates.append(update.status),
    )

    assert result.status == "passed"
    assert [step.status for step in result.steps] == ["passed"] * 5
    assert driver.actions[-1] == "close"
    assert result.report_path is not None
    report_path = Path(result.report_path)
    assert report_path.is_file()
    assert report_path.name == "index.html"
    assert (report_path.parent / "assets" / "screenshots" / "005_after_login.png").is_file()
    assert "running" in updates
    assert updates[-1] == "passed"


def test_runner_saves_screenshot_on_step_failure(tmp_path: Path) -> None:
    test_file = tmp_path / "test.yaml"
    write_test(test_file, [{"click": "Missing"}, {"expect_text": "Later step"}])
    driver = FakeDriver({}, tmp_path / "run", fail_on="Missing")

    result = run_test(
        test_file,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        driver=driver,
    )

    assert result.status == "failed"
    assert [step.status for step in result.steps] == ["failed", "skipped"]
    assert result.steps[0].screenshot is not None
    assert Path(result.steps[0].screenshot).is_file()
    assert driver.actions[-1] == "close"


def test_runner_suggests_locator_repair_without_applying_it(tmp_path: Path) -> None:
    test_file = tmp_path / "test.yaml"
    write_test(test_file, [{"click": "Old"}])
    driver = FakeDriver({}, tmp_path / "run", fail_on="Old")

    result = run_test(
        test_file,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        database_path=tmp_path / "history.sqlite",
        driver=driver,
    )

    assert result.status == "failed"
    assert result.steps[0].new_locator == "New"
    assert result.metadata["healing_suggestions"][0]["method"] == "fuzzy_text_label"
    from testbench.core.database import RunStore

    assert RunStore(tmp_path / "history.sqlite").list_healing()[0]["accepted"] is None


def test_runner_auto_heals_only_non_assertion_steps(tmp_path: Path) -> None:
    click_path = tmp_path / "click.yaml"
    click_path.write_text(
        yaml.safe_dump(
            {
                "name": "auto",
                "target": "web",
                "config": {"healing": "auto"},
                "steps": [{"click": "Old"}],
            }
        ),
        encoding="utf-8",
    )
    driver = FakeDriver({}, tmp_path / "run", fail_on="Old")
    result = run_test(
        click_path,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        driver=driver,
    )
    assert result.status == "passed"
    assert result.steps[0].status == "healed"
    assert "click:New" in driver.actions

    class AssertionDriver(FakeDriver):
        def expect_visible(self, target):
            raise LookupError("Could not find a visible element matching 'Old'.")

    assertion_path = tmp_path / "assertion.yaml"
    assertion_path.write_text(
        yaml.safe_dump(
            {
                "name": "assert",
                "target": "web",
                "config": {"healing": "auto"},
                "steps": [{"expect_visible": "Old"}],
            }
        ),
        encoding="utf-8",
    )
    assertion_driver = AssertionDriver({}, tmp_path / "assert-run")
    assertion = run_test(
        assertion_path,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        driver=assertion_driver,
    )
    assert assertion.status == "failed"
    assert not assertion.metadata.get("healing_suggestions")
    assert "visible:New" not in assertion_driver.actions


def test_new_target_is_loaded_by_driver_convention(tmp_path: Path, monkeypatch) -> None:
    module = ModuleType("testbench.drivers.custom_target")
    module.CustomTargetDriver = FakeDriver
    monkeypatch.setitem(sys.modules, module.__name__, module)

    driver = _create_driver("custom_target", {}, tmp_path / "run")

    assert isinstance(driver, FakeDriver)


def test_runner_maps_android_steps_to_driver_methods(tmp_path: Path) -> None:
    test_file = tmp_path / "android.yaml"
    test_file.write_text(
        yaml.safe_dump(
            {
                "name": "Android actions",
                "target": "android",
                "config": {"package": "com.example.app"},
                "steps": [
                    {"swipe": {"direction": "up"}},
                    {"scroll_to": "Display"},
                    {"back": True},
                    {"home": True},
                    {"hide_keyboard": True},
                    {"press_key": "enter"},
                    {"set_orientation": "landscape"},
                ],
            }
        ),
        encoding="utf-8",
    )
    driver = FakeDriver({}, tmp_path / "run")

    result = run_test(
        test_file,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        driver=driver,
    )

    assert result.status == "passed"
    assert driver.actions[:7] == [
        "swipe:up",
        "scroll_to:Display",
        "back",
        "home",
        "hide_keyboard",
        "press_key:enter",
        "orientation:landscape",
    ]


def test_android_metadata_is_included_in_report_and_database(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "android.yaml"
    test_file.write_text(
        yaml.safe_dump(
            {
                "name": "Settings",
                "target": "android",
                "config": {"package": "com.android.settings"},
                "steps": [{"screenshot": "home"}],
            }
        ),
        encoding="utf-8",
    )
    driver = FakeDriver({}, tmp_path / "runs" / "metadata")
    driver.metadata = {
        "device_model": "Pixel 8",
        "android_version": "15",
        "app_package": "com.android.settings",
    }

    result = run_test(
        test_file,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        database_path=tmp_path / "history.sqlite3",
        driver=driver,
    )

    report = Path(result.report_path).read_text(encoding="utf-8")
    from testbench.core.database import RunStore

    saved = RunStore(tmp_path / "history.sqlite3").get(result.run_id)
    assert "Pixel 8" in report
    assert "Android version" in report
    assert "com.android.settings" in report
    assert saved["metadata"]["device_model"] == "Pixel 8"

import base64
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from testbench.api import main
from testbench.core.database import RunStore
from testbench.core.runner import RunResult


def test_api_lists_tests_and_rejects_unknown_run(tmp_path: Path, monkeypatch) -> None:
    tests_dir = tmp_path / "test-files"
    tests_dir.mkdir()
    (tests_dir / "sample.yaml").write_text("name: sample\n", encoding="utf-8")
    monkeypatch.setattr(main, "TESTS_DIR", tests_dir)
    monkeypatch.setattr(main, "STORE", RunStore(tmp_path / "history.sqlite3"))
    client = TestClient(main.app)

    listed = client.get("/api/tests")
    invalid_run = client.post("/api/tests/../outside/run")
    dashboard = client.get("/")
    dashboard_script = client.get("/static/dashboard.js")
    dashboard_style = client.get("/static/dashboard.css")
    inspector_page = client.get("/mobile-inspector")
    inspector_script = client.get("/static/mobile-inspector.js")
    inspector_style = client.get("/static/mobile-inspector.css")
    game_page = client.get("/game-template-maker")
    game_script = client.get("/static/game-template-maker.js")
    recorder_page = client.get("/recorder")
    recorder_script = client.get("/static/recorder.js")
    ai_page = client.get("/ai-writer")
    ai_script = client.get("/static/ai-writer.js")

    assert listed.status_code == 200
    assert listed.json() == [{"name": "sample", "path": "sample.yaml"}]
    assert invalid_run.status_code in {400, 404}
    assert dashboard.status_code == 200
    assert "TestBench" in dashboard.text
    assert "tailwindcss.com" not in dashboard.text
    assert dashboard_script.status_code == 200
    assert dashboard_style.status_code == 200
    assert inspector_page.status_code == 200
    assert "Mobile Inspector" in inspector_page.text
    assert "/api/mobile/inspect" in inspector_script.text
    assert inspector_script.status_code == 200
    assert inspector_style.status_code == 200
    assert game_page.status_code == 200
    assert "Game Template Maker" in game_page.text
    assert game_script.status_code == 200
    assert "/api/game/templates" in game_script.text
    assert recorder_page.status_code == 200
    assert "Start Recording" in recorder_page.text
    assert "Add step" in recorder_page.text
    assert recorder_script.status_code == 200
    assert "/api/recorder/events" in recorder_script.text
    assert ai_page.status_code == 200
    assert "AI Test Writer" in ai_page.text
    assert ai_script.status_code == 200
    assert "/api/ai/generate" in ai_script.text


def test_ai_api_config_consent_and_yaml_validation(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "testbench.toml"
    config_path.write_text("[ai]\nenabled = false\nprovider = 'ollama'\n", encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_CONFIG", str(config_path))
    client = TestClient(main.app)

    assert client.get("/api/ai/config").json()["enabled"] is False
    consent = client.post(
        "/api/ai/consent",
        json={"provider": "ollama", "accepted": True},
    )
    valid = client.post(
        "/api/ai/validate",
        json={"yaml": "name: draft\ntarget: web\nsteps:\n  - open: https://example.test\n"},
    )
    invalid = client.post(
        "/api/ai/validate",
        json={"yaml": "name: bad\ntarget: web\nsteps:\n  - unknown: true\n"},
    )

    assert consent.status_code == 200
    assert 'consented_providers = ["ollama"]' in config_path.read_text(encoding="utf-8")
    assert "secret" not in config_path.read_text(encoding="utf-8")
    assert valid.status_code == 200
    assert invalid.status_code == 422


def test_recorder_flakiness_warnings_inspect_nested_locators() -> None:
    warnings = main._flakiness_warnings(
        [
            {"click": {"role": {"role": "button", "name": "Continue"}}},
            {"click": {"xpath": "//button[1]"}},
        ]
    )

    assert len(warnings) == 1
    assert "CSS/XPath" in warnings[0]


def test_database_saves_and_lists_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "history.sqlite3")
    from testbench.core.runner import StepResult

    result = RunResult(
        run_id="a" * 32,
        test_name="database check",
        target="web",
        status="passed",
        started_at="2026-10-27T00:00:00+00:00",
        duration_seconds=0.25,
        steps=[StepResult(1, "open", "passed", 0.1)],
        artifact_dir=str(tmp_path / "artifacts"),
    )
    store.save(result)

    assert store.get(result.run_id)["status"] == "passed"
    assert store.list()[0]["steps"][0]["action"] == "open"


def test_api_starts_and_polls_background_run(tmp_path: Path, monkeypatch) -> None:
    tests_dir = tmp_path / "test-files"
    tests_dir.mkdir()
    (tests_dir / "sample.yaml").write_text("name: sample\n", encoding="utf-8")
    runs_dir = tmp_path / "runs"
    reports_dir = tmp_path / "reports"
    database_path = runs_dir / "history.sqlite3"
    monkeypatch.setattr(main, "TESTS_DIR", tests_dir)
    monkeypatch.setattr(main, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(main, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    monkeypatch.setattr(main, "STORE", RunStore(database_path))

    def fake_run(
        test_path,
        *,
        runs_dir,
        reports_dir,
        database_path,
        run_id,
        on_progress,
    ):
        result = RunResult(
            run_id=run_id,
            test_name=test_path.stem,
            target="web",
            status="running",
            started_at="2026-10-27T00:00:00+00:00",
            duration_seconds=0.1,
            artifact_dir=str(runs_dir / run_id),
        )
        on_progress(result)
        result.status = "passed"
        result.report_path = str(reports_dir / run_id / "index.html")
        on_progress(result)
        return result

    monkeypatch.setattr(main, "run_test", fake_run)
    client = TestClient(main.app)

    started = client.post("/api/tests/sample/run")

    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "passed"
    assert client.get("/api/runs").json()[0]["run_id"] == run_id


def test_api_lists_and_starts_android_devices(monkeypatch) -> None:
    class FakeDeviceManager:
        def list_devices(self):
            return [{"serial": "emulator-5554", "type": "emulator"}]

        def list_avds(self):
            return ["Pixel_API_35"]

        def start_emulator(self, avd_name):
            assert avd_name == "Pixel_API_35"
            return "emulator-5554"

    monkeypatch.setattr(main, "DeviceManager", FakeDeviceManager)
    client = TestClient(main.app)

    assert client.get("/api/devices").json() == {
        "devices": [{"serial": "emulator-5554", "type": "emulator"}],
        "avds": ["Pixel_API_35"],
    }
    started = client.post("/api/devices/emulator/start", json={"avd_name": "Pixel_API_35"})
    assert started.status_code == 200
    assert started.json()["serial"] == "emulator-5554"


def test_mobile_inspector_returns_screenshot_and_elements(tmp_path: Path, monkeypatch) -> None:
    class FakeMobileDriver:
        def __init__(self, config, artifact_dir):
            self.artifact_dir = artifact_dir

        def open(self, destination):
            assert destination == ""

        def inspect(self, screenshot_path):
            screenshot_path.write_bytes(b"png content")
            return {
                "screenshot_path": str(screenshot_path),
                "elements": [{"text": "Settings", "clickable": True}],
                "metadata": {"device_model": "Pixel"},
            }

        def close(self):
            pass

    monkeypatch.setattr(main, "MobileDriver", FakeMobileDriver)
    payload = TestClient(main.app).get("/api/mobile/inspect").json()

    assert payload["screenshot"] == ("data:image/png;base64," + base64.b64encode(b"png content").decode("ascii"))
    assert payload["elements"] == [{"text": "Settings", "clickable": True}]
    assert payload["metadata"]["device_model"] == "Pixel"


def test_game_screenshot_and_template_crop_api(tmp_path: Path, monkeypatch) -> None:
    frame = np.zeros((60, 80, 3), dtype=np.uint8)
    frame[10:30, 20:50] = (20, 120, 240)
    templates_dir = tmp_path / "templates"
    monkeypatch.setattr(main, "GAME_TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(main, "_capture_game_frame", lambda config: frame)
    client = TestClient(main.app)

    screenshot = client.get("/api/game/screenshot?surface=browser&url=https://example.test")
    saved = client.post(
        "/api/game/templates",
        json={
            "x": 20,
            "y": 10,
            "width": 30,
            "height": 20,
            "name": "start_button",
            "surface": "desktop",
        },
    )
    templates = client.get("/api/game/templates")

    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert saved.status_code == 200
    assert saved.json()["yaml_step"] == "- click_image: start_button.png"
    assert cv2.imread(str(templates_dir / "start_button.png")).shape[:2] == (20, 30)
    assert templates.json()[0]["name"] == "start_button.png"


def test_game_template_maker_can_rename_a_recorded_template(tmp_path: Path, monkeypatch) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "click_001.png").write_bytes(b"png")
    monkeypatch.setattr(main, "GAME_TEMPLATES_DIR", templates_dir)

    response = TestClient(main.app).post(
        "/api/game/templates/click_001.png/rename",
        json={"name": "start_button"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "start_button.png"
    assert response.json()["yaml_step"] == "- click_image: start_button.png"
    assert not (templates_dir / "click_001.png").exists()
    assert (templates_dir / "start_button.png").read_bytes() == b"png"


def test_recorder_api_lifecycle_and_save(tmp_path: Path, monkeypatch) -> None:
    from testbench.recorder.base import RawEvent, Recorder
    from testbench.recorder.step_builder import StepBuilder

    class FakeRecorder(Recorder):
        def start(self, config):
            self.emit(RawEvent("click", 1.0, {"locator": "Continue"}))

        def stop(self):
            return StepBuilder().build(self.raw_events)

        def pause(self):
            self.paused = True

        def resume(self):
            self.paused = False

    tests_dir = tmp_path / "tests"
    runs_dir = tmp_path / "runs"
    tests_dir.mkdir()
    monkeypatch.setattr(main, "TESTS_DIR", tests_dir)
    monkeypatch.setattr(main, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(main, "_recorder_state", None)
    monkeypatch.setattr(main, "WebRecorder", FakeRecorder)
    monkeypatch.setattr(main, "_recorder_self_check", lambda state: None)
    client = TestClient(main.app)

    started = client.post(
        "/api/recorder/start",
        json={"target": "web", "config": {}, "name": "Captured"},
    )
    assert started.status_code == 202
    assert started.json()["status"] == "recording"
    stopped = client.post("/api/recorder/stop")
    assert stopped.status_code == 200
    assert stopped.json()["steps"] == [{"click": "Continue"}]
    saved = client.post("/api/recorder/save", json={"name": "Captured"})
    assert saved.status_code == 200
    assert (tests_dir / "Captured.yaml").read_text(encoding="utf-8").find("click: Continue") >= 0
    assert client.get("/api/game/templates/start_button.png/image").status_code == 200


def test_game_template_api_rejects_path_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "_capture_game_frame", lambda config: np.zeros((20, 20, 3), dtype=np.uint8))
    client = TestClient(main.app)

    response = client.post(
        "/api/game/templates",
        json={"x": 0, "y": 0, "width": 5, "height": 5, "name": "../outside"},
    )

    assert response.status_code == 422


def test_game_template_match_endpoint(monkeypatch) -> None:
    class FakeGameDriver:
        def __init__(self, config, artifact_dir, recording):
            assert recording is False

        def open(self, destination):
            assert destination == "https://example.test/game"

        def match_now(self, name):
            assert name == "button.png"
            return {"found": True, "confidence": 0.96, "x": 30, "y": 20}

        def close(self):
            pass

    monkeypatch.setattr(main, "GameDriver", FakeGameDriver)
    monkeypatch.setattr(main, "GAME_TEMPLATES_DIR", Path("unused"))
    response = TestClient(main.app).post(
        "/api/game/templates/button.png/match?surface=browser&url=https://example.test/game"
    )

    assert response.status_code == 200
    assert response.json()["confidence"] == 0.96

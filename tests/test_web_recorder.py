from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from testbench.core.runner import run_test
from testbench.recorder.web_recorder import WebRecorder
from testbench.recorder.yaml_export import dump_test_yaml

DEMO_DIR = Path(__file__).resolve().parents[1] / "tests_examples" / "recorder_demo"
EXPECTED_PATH = DEMO_DIR / "expected.yaml"


@pytest.mark.recorder
def test_web_recorder_captures_sensitive_input_and_form_interactions(
    tmp_path: Path,
    monkeypatch,
) -> None:

    handler = partial(SimpleHTTPRequestHandler, directory=str(DEMO_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    recorder = WebRecorder()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        recorder.start(
            {
                "url": url,
                "headless": True,
                "artifact_dir": str(tmp_path / "capture"),
            }
        )
        assert recorder.page is not None
        recorder.page.get_by_label("Email address").fill("qa@example.test")
        recorder.page.wait_for_timeout(350)
        recorder.page.get_by_label("Password").fill("do-not-record-this")
        recorder.page.wait_for_timeout(350)
        recorder.page.get_by_label("Plan").select_option(label="Professional")
        recorder.page.get_by_label("Accept terms").check()
        recorder.page.get_by_role("button", name="Continue").click()
        recorder.page.wait_for_timeout(100)

        steps = recorder.stop()
        generated = dump_test_yaml(
            name="Recorded form submission",
            target="web",
            config={"browser": "chromium", "url": url},
            variables=recorder.variables,
            steps=steps,
        )
        generated_document = yaml.safe_load(generated)
        expected_document = yaml.safe_load(
            EXPECTED_PATH.read_text(encoding="utf-8").replace(
                "http://127.0.0.1:8001/index.html",
                url,
            )
        )
        assert generated_document == expected_document

        assert [step.action for step in steps].count("type") == 2
        password_step = next(
            step for step in steps if step.action == "type" and step.value["field"]["role"]["name"] == "Password"
        )
        assert password_step.value["value"] == "${LOGIN_PASSWORD}"
        assert recorder.variables == ["LOGIN_PASSWORD"]
        assert all("do-not-record-this" not in repr(event.to_dict()) for event in recorder.raw_events)
        assert any(step.action == "select_option" for step in steps)
        assert any(step.action == "click" for step in steps)
        assert list((tmp_path / "capture" / "screenshots").glob("*.png"))

        monkeypatch.setenv("LOGIN_PASSWORD", "test-password")
        recorded_path = tmp_path / "recorded.yaml"
        recorded_path.write_text(generated, encoding="utf-8")
        result = run_test(
            recorded_path,
            runs_dir=tmp_path / "runs",
            reports_dir=tmp_path / "reports",
        )
        assert result.status == "passed", [(step.action, step.error) for step in result.steps]
        assert result.report_path is not None
    finally:
        if recorder.page is not None:
            recorder.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

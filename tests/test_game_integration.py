from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from testbench.core.runner import run_test

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = PROJECT_ROOT / "tests_examples"
EXAMPLE_PATH = DEMO_DIR / "game_example.yaml"


@pytest.mark.game
def test_browser_game_example_against_local_canvas(tmp_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        pytest.skip(f"Playwright Chromium is unavailable: {exc}")

    handler = partial(SimpleHTTPRequestHandler, directory=str(DEMO_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yaml_data = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{port}/game_demo/index.html"
        yaml_data["config"]["url"] = url
        yaml_data["config"]["templates_dir"] = str(tmp_path / "templates")
        yaml_data["steps"][0]["open"] = url
        template_dir = Path(yaml_data["config"]["templates_dir"])
        template_dir.mkdir(parents=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 960, "height": 640})
            page.goto(url)
            bounds = page.locator("#game").bounding_box()
            assert bounds is not None
            screenshot = page.screenshot(
                clip={
                    "x": bounds["x"] + 250,
                    "y": bounds["y"] + 180,
                    "width": 300,
                    "height": 110,
                }
            )
            (template_dir / "start_button.png").write_bytes(screenshot)
            browser.close()

        test_path = tmp_path / "game_example.yaml"
        test_path.write_text(yaml.safe_dump(yaml_data), encoding="utf-8")
        result = run_test(
            test_path,
            runs_dir=tmp_path / "runs",
            reports_dir=tmp_path / "reports",
        )

        assert result.status == "passed", [(step.action, step.status, step.error) for step in result.steps]
        assert result.report_path is not None
        assert Path(result.report_path).is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

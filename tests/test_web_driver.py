from pathlib import Path

from testbench.drivers.web import WebDriver


def test_web_driver_prefers_exact_button_text_and_records_artifacts(
    tmp_path: Path,
) -> None:
    page_file = tmp_path / "page.html"
    page_file.write_text(
        """<!doctype html>
<label for="email">Email</label><input id="email">
<h1>Login Page</h1>
<button onclick="document.querySelector('#result').textContent='Dashboard'">Login</button>
<p id="result"></p>
""",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    driver = WebDriver({"browser": "chromium"}, artifacts)
    try:
        driver.open(page_file.as_uri())
        driver.type("Email", "person@example.com")
        driver.click("Login")
        driver.expect_text("Dashboard")
        driver.screenshot(artifacts / "dashboard.png")
    finally:
        driver.close()

    assert (artifacts / "trace.zip").is_file()
    assert (artifacts / "dashboard.png").is_file()
    assert list((artifacts / "videos").glob("*.webm"))

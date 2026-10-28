import json
from pathlib import Path

from fastapi.testclient import TestClient

from testbench.api import main
from testbench.core.reporter import generate_report
from testbench.core.runner import RunResult, StepResult
from testbench.setup.project import initialize_project


def test_init_is_idempotent_and_does_not_overwrite(tmp_path: Path):
    created = initialize_project(tmp_path / "demo")
    sample = tmp_path / "demo" / "tests" / "sample_web.yaml"
    original = sample.read_text(encoding="utf-8")

    assert "tests/" in created
    assert "templates/" in created
    assert "apps/" in created
    assert "https://the-internet.herokuapp.com/login" in original
    assert initialize_project(tmp_path / "demo") == []
    assert sample.read_text(encoding="utf-8") == original


def test_dashboard_rejects_cross_origin_writes_and_outside_template_path(tmp_path):
    client = TestClient(main.app)
    cross_origin = client.post(
        "/api/recorder/pause",
        headers={"Origin": "https://attacker.example"},
    )
    outside_path = client.get(
        "/api/game/templates",
        params={"templates_dir": str(tmp_path / "outside")},
    )

    assert cross_origin.status_code == 403
    assert outside_path.status_code == 400


def test_dashboard_requires_token_when_remote_access_is_configured(monkeypatch):
    monkeypatch.setenv("TESTBENCH_AUTH_TOKEN", "release-test-token")
    client = TestClient(main.app)

    assert client.get("/").status_code == 401
    assert (
        client.get(
            "/",
            headers={"Authorization": "Bearer release-test-token"},
        ).status_code
        == 200
    )


def test_report_redacts_sensitive_error_text(tmp_path: Path):
    result = RunResult(
        run_id="a" * 32,
        test_name="Privacy check",
        target="web",
        status="failed",
        started_at="2026-01-01T00:00:00+00:00",
        duration_seconds=0.1,
        steps=[
            StepResult(
                index=1,
                action="click",
                status="failed",
                duration_seconds=0.1,
                error="password=clear-text qa@example.com",
            )
        ],
        artifact_dir=str(tmp_path / "run"),
    )
    report = generate_report(result, tmp_path / "reports").read_text(encoding="utf-8")

    assert "clear-text" not in report
    assert "qa@example.com" not in report
    assert "[REDACTED]" in report


def test_doctor_json_is_machine_readable(capsys, monkeypatch):
    from testbench.setup import doctor

    monkeypatch.setattr(doctor, "_playwright_browser_available", lambda: True)
    assert doctor.run_doctor(json_output=True) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["ok"] is True
    assert any(item["name"] == "Playwright Chromium" for item in data["checks"])

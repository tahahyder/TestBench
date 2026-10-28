from pathlib import Path

from testbench.core.reporter import generate_report
from testbench.core.runner import RunResult


def test_game_report_lists_surface_matches_fps_and_fallback(tmp_path: Path) -> None:
    artifacts = tmp_path / "run-artifacts"
    annotation = artifacts / "annotated" / "start.png"
    recording = artifacts / "game_recording.mp4"
    annotation.parent.mkdir(parents=True)
    annotation.write_bytes(b"png")
    recording.write_bytes(b"mp4")
    result = RunResult(
        run_id="c" * 32,
        test_name="Game screen check",
        target="game",
        status="passed",
        started_at="2026-10-27T00:00:00+00:00",
        duration_seconds=2,
        artifact_dir=str(artifacts),
        metadata={
            "surface": "browser",
            "resolution": "960x640",
            "matches": [
                {
                    "image": "start_button.png",
                    "confidence": 0.97,
                    "annotated": str(annotation),
                }
            ],
            "fps": [
                {
                    "seconds": 2,
                    "fps": 45.5,
                    "min_fps": 30,
                    "passed": True,
                }
            ],
            "ai_fallback_uses": [],
        },
    )

    report_path = generate_report(result, tmp_path / "reports")
    report = report_path.read_text(encoding="utf-8")

    assert "Game details" in report
    assert "browser" in report
    assert "960x640" in report
    assert "start_button.png" in report
    assert "0.970" in report
    assert "45.50" in report
    assert "AI vision fallback:</strong> not used" in report
    assert "game_recording.mp4" in report
    assert (report_path.parent / "assets" / "annotated" / "start.png").is_file()

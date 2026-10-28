import sqlite3
from pathlib import Path

from testbench.core.database import RunStore
from testbench.core.reporter import generate_report
from testbench.core.runner import RunResult


def test_android_report_links_local_recording_logcat_and_failure_source(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "runs" / "android-run"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "screen_recording.mp4").write_bytes(b"mp4")
    (artifact_dir / "logcat.txt").write_text("logcat", encoding="utf-8")
    (artifact_dir / "failure_step_002.xml").write_text("<hierarchy />", encoding="utf-8")
    result = RunResult(
        run_id="b" * 32,
        test_name="Settings test",
        target="android",
        status="failed",
        started_at="2026-10-27T00:00:00+00:00",
        duration_seconds=1,
        artifact_dir=str(artifact_dir),
        metadata={
            "device_model": "Pixel",
            "android_version": "15",
            "app_package": "com.android.settings",
        },
    )

    report_path = generate_report(result, tmp_path / "reports")
    report = report_path.read_text(encoding="utf-8")

    assert "Pixel" in report
    assert "Android version" in report
    assert "com.android.settings" in report
    assert "screen_recording.mp4" in report
    assert "logcat.txt" in report
    assert "Failure page source" in report
    assert (report_path.parent / "assets" / "screen_recording.mp4").is_file()
    assert (report_path.parent / "assets" / "failure_step_002.xml").is_file()


def test_run_store_migrates_existing_database_without_mobile_metadata(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE runs (
                run_id VARCHAR PRIMARY KEY, test_name VARCHAR NOT NULL,
                target VARCHAR NOT NULL, status VARCHAR NOT NULL,
                started_at VARCHAR NOT NULL, duration_seconds FLOAT NOT NULL,
                artifact_dir VARCHAR NOT NULL, report_path VARCHAR
            )"""
        )
        connection.execute(
            """CREATE TABLE run_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR NOT NULL,
                "index" INTEGER NOT NULL, action VARCHAR NOT NULL,
                status VARCHAR NOT NULL, duration_seconds FLOAT NOT NULL,
                error VARCHAR, screenshot VARCHAR
            )"""
        )

    store = RunStore(database_path)

    assert store.list() == []

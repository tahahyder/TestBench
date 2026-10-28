import json
import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from testbench import cli
from testbench.core.runner import RunResult


def _write_test(path: Path, tag: str) -> None:
    path.write_text(
        f"name: {path.stem}\ntarget: web\ntags: [{tag}]\nsteps:\n  - open: https://example.com\n",
        encoding="utf-8",
    )


def test_folder_tag_filter_and_ci_outputs(tmp_path: Path, monkeypatch):
    import testbench.core.runner as runner

    tests = tmp_path / "tests"
    tests.mkdir()
    _write_test(tests / "smoke.yaml", "smoke")
    _write_test(tests / "other.yaml", "slow")
    calls = []

    def fake_run(path):
        calls.append(Path(path).name)
        return RunResult(
            run_id=len(calls).to_bytes(16, "big").hex(),
            test_name=Path(path).stem,
            target="web",
            status="passed",
            started_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.1,
        )

    monkeypatch.setattr(runner, "run_test", fake_run)
    junit = tmp_path / "junit.xml"
    json_results = tmp_path / "results.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            str(tests),
            "--tag",
            "smoke",
            "--workers",
            "2",
            "--junit",
            str(junit),
            "--json-results",
            str(json_results),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["smoke.yaml"]
    assert ET.parse(junit).getroot().attrib["tests"] == "1"
    assert json.loads(json_results.read_text(encoding="utf-8"))[0]["test_name"] == "smoke"


def test_retry_marks_test_flaky_and_exits_successfully(tmp_path: Path, monkeypatch):
    import testbench.core.database as database
    import testbench.core.reporter as reporter
    import testbench.core.runner as runner

    test = tmp_path / "retry.yaml"
    _write_test(test, "smoke")
    calls = []

    def fake_run(path):
        calls.append(1)
        return RunResult(
            run_id=len(calls).to_bytes(16, "big").hex(),
            test_name="retry",
            target="web",
            status="failed" if len(calls) == 1 else "passed",
            started_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.1,
            artifact_dir=str(tmp_path),
        )

    class FakeStore:
        def __init__(self, _path):
            pass

        def save(self, _result):
            return None

    monkeypatch.setattr(runner, "run_test", fake_run)
    monkeypatch.setattr(reporter, "generate_report", lambda *_args: tmp_path / "index.html")
    monkeypatch.setattr(database, "RunStore", FakeStore)
    result = CliRunner().invoke(cli.app, ["run", str(test), "--retries", "1"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert "FLAKY" in result.output

from pathlib import Path

import pytest

from testbench.ai.config import load_ai_config
from testbench.ai.generate import generate_test
from testbench.ai.providers import create_provider
from testbench.core.runner import run_test
from testbench.core.steps import Step
from testbench.recorder.yaml_export import dump_test_yaml


@pytest.mark.ai
def test_ai_generates_and_runs_against_local_recorder_demo(tmp_path: Path, monkeypatch):
    config = load_ai_config()
    if not config.get("enabled"):
        pytest.skip("AI is disabled in testbench.toml.")
    if config.get("provider") not in config.get("consented_providers", []):
        pytest.skip("No provider consent has been recorded.")
    healthy, explanation = create_provider(config).health(str(config.get("model", "")))
    if not healthy:
        pytest.skip(explanation)

    demo = Path(__file__).resolve().parents[1] / "tests_examples" / "recorder_demo" / "index.html"
    url = demo.as_uri()
    from testbench.ai.client import AIClient

    client = AIClient(config, database_path=tmp_path / "ai.sqlite")
    generated = generate_test(
        "Open the local recorder demo and verify the Email address field is visible.",
        target="web",
        url=url,
        client=client,
        run_id="local-ai-integration",
    )
    for variable in generated["variables"]:
        monkeypatch.setenv(variable, "fake-value@example.test")
    test_file = tmp_path / "generated.yaml"
    test_file.write_text(
        dump_test_yaml(
            name=generated["name"],
            target=generated["target"],
            config=generated["config"],
            variables=generated["variables"],
            steps=[Step.model_validate(item) for item in generated["steps"]],
        ),
        encoding="utf-8",
    )
    result = run_test(
        test_file,
        runs_dir=tmp_path / "runs",
        reports_dir=tmp_path / "reports",
        database_path=tmp_path / "history.sqlite",
        ai_run_id="local-ai-integration",
        ai_generated=True,
    )
    assert result.status == "passed", [(step.action, step.error) for step in result.steps]

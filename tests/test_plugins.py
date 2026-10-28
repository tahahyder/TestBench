from pathlib import Path

from pydantic import BaseModel

from testbench.core.runner import RunResult, _create_driver, _execute_step, _run_hooks
from testbench.core.steps import Step
from testbench.plugins import clear_plugin_cache


class MessageModel(BaseModel):
    message: str


class CustomStep:
    model = MessageModel
    executed = []

    def execute(self, driver, value):
        self.executed.append((driver, value))


class CustomDriver:
    def __init__(self, *, config, artifact_dir):
        self.config = config
        self.artifact_dir = artifact_dir


class Hook:
    calls = []

    def after_run(self, result):
        self.calls.append(result.run_id)


class FakeEntryPoint:
    def __init__(self, name, loaded):
        self.name = name
        self.loaded = loaded

    def load(self):
        return self.loaded


class FakeEntryPoints:
    groups = {}

    def select(self, *, group):
        return self.groups.get(group, ())


def test_entry_point_steps_drivers_and_hooks_load(monkeypatch, tmp_path: Path):
    import testbench.plugins as plugins

    FakeEntryPoints.groups = {
        "testbench.drivers": [FakeEntryPoint("custom", CustomDriver)],
        "testbench.steps": [FakeEntryPoint("notify", CustomStep)],
        "testbench.hooks": [FakeEntryPoint("example", Hook)],
    }
    monkeypatch.setattr(plugins, "entry_points", lambda: FakeEntryPoints())
    clear_plugin_cache()
    CustomStep.executed.clear()
    Hook.calls.clear()

    step = Step.model_validate({"notify": {"message": "done"}})
    driver = _create_driver("custom", {"option": True}, tmp_path)
    _execute_step(driver, step, tmp_path, 1)

    result = RunResult("run-1", "demo", "custom", "passed", "", 0)
    _run_hooks(result, "after_run")
    assert CustomStep.executed == [(driver, {"message": "done"})]
    assert Hook.calls == ["run-1"]
    clear_plugin_cache()

from pathlib import Path

import numpy as np
import pytest
import yaml

from testbench.core.steps import Step
from testbench.drivers.mobile import _redact_sensitive_xml
from testbench.recorder.base import RawEvent
from testbench.recorder.export import export_pytest_playwright
from testbench.recorder.game_recorder import GameRecorder
from testbench.recorder.mobile_recorder import _elements_at_point, _mobile_locator
from testbench.recorder.step_builder import StepBuilder
from testbench.recorder.yaml_export import dump_test_yaml

ANDROID_XML = """<hierarchy>
  <node class="android.widget.Button" text="Open" resource-id="com.example:id/open"
        content-desc="" clickable="true" bounds="[10,20][200,100]">
    <node class="android.widget.Button" text="Open" resource-id="com.example:id/open_inner"
          content-desc="open-control" clickable="true" bounds="[30,35][90,80]" />
  </node>
  <node class="android.widget.EditText" text="private-value" password="true"
        resource-id="com.example:id/password" bounds="[0,110][200,160]" />
</hierarchy>"""


def test_android_tap_mapping_selects_deepest_clickable_locator() -> None:
    elements = _elements_at_point(ANDROID_XML, 50, 50, 400, 800)

    assert len(elements) == 2
    assert _mobile_locator(elements[0]) == {"accessibility_id": "open-control"}
    assert _mobile_locator(elements[1]) == "Open"


def test_android_page_source_redacts_password_values() -> None:
    redacted = _redact_sensitive_xml(ANDROID_XML)

    assert "private-value" not in redacted
    assert "open-control" in redacted


def test_export_writes_page_object_style_playwright_python(tmp_path: Path) -> None:
    test_path = tmp_path / "web.yaml"
    test_path.write_text(
        yaml.safe_dump(
            {
                "name": "Login works",
                "target": "web",
                "config": {},
                "variables": ["LOGIN_PASSWORD"],
                "steps": [
                    {"open": "https://example.test"},
                    {"type": {"field": "Password", "value": "${LOGIN_PASSWORD}"}},
                    {"click": "Continue"},
                    {"expect_text": "Welcome"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    generated = export_pytest_playwright(test_path)
    compile(generated, str(test_path.with_suffix(".py")), "exec")

    assert "class RecordedPage" in generated
    assert "def test_login_works(page):" in generated
    assert "os.environ.get(name)" in generated
    assert "flow.locator('Continue').click()" in generated


def test_export_rejects_non_web_targets(tmp_path: Path) -> None:
    test_path = tmp_path / "android.yaml"
    test_path.write_text(
        "name: Android\ntarget: android\nconfig:\n  package: com.example.app\nsteps:\n  - open: settings\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not supported"):
        export_pytest_playwright(test_path)


def test_game_recorder_saves_click_template_as_image_step(tmp_path: Path) -> None:
    class FakeSurface:
        def capture(self):
            return np.zeros((120, 160, 3), dtype=np.uint8)

    recorder = GameRecorder()
    recorder._surface = FakeSurface()
    recorder.templates_dir = tmp_path / "templates"
    recorder.artifact_dir = tmp_path / "artifacts"
    recorder._save_click(80, 60)

    steps = StepBuilder().build(recorder.raw_events)

    assert [step.model_dump() for step in steps] == [{"action": "click_image", "value": "click_001.png"}]
    assert (tmp_path / "templates" / "click_001.png").is_file()
    assert Step.model_validate({"click_image": "click_001.png"}).action == "click_image"


def test_yaml_export_has_stable_sorted_variables() -> None:
    steps = StepBuilder().build(
        [
            RawEvent("navigation", 0.1, value="https://example.test"),
            RawEvent("keypress", 0.2, value="enter"),
        ]
    )

    rendered = dump_test_yaml(
        name="Recorded",
        target="web",
        config={},
        variables=["ZED", "ALPHA", "ZED"],
        steps=steps,
    )

    assert rendered.index("- ALPHA") < rendered.index("- ZED")

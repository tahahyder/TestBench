from pathlib import Path

import cv2
import numpy as np
import pytest

from testbench.core.runner import _execute_step
from testbench.core.steps import GameConfig, Step, TestSpec
from testbench.drivers.game import GameDriver
from testbench.drivers.surfaces.fake import FakeSurface


def _scene() -> tuple[np.ndarray, np.ndarray]:
    template = np.zeros((18, 22, 3), dtype=np.uint8)
    cv2.rectangle(template, (1, 1), (20, 16), (0, 220, 40), 2)
    cv2.line(template, (4, 4), (17, 13), (255, 255, 255), 2)
    scene = np.zeros((100, 160, 3), dtype=np.uint8)
    scene[30:48, 70:92] = template
    return scene, template


def _driver(tmp_path: Path, frames: list[np.ndarray]) -> tuple[GameDriver, FakeSurface]:
    surface = FakeSurface(frames)
    driver = GameDriver(
        {
            "surface": "android",
            "templates_dir": str(tmp_path),
            "step_timeout": 0.05,
        },
        tmp_path / "artifacts",
        surface=surface,
    )
    driver.open("")
    return driver, surface


def test_game_config_validates_surface_requirements() -> None:
    config = GameConfig.model_validate({"surface": "browser", "url": "https://example.test/game"})
    assert config.match_threshold == 0.85
    with pytest.raises(ValueError, match="config.url"):
        GameConfig.model_validate({"surface": "browser"})
    with pytest.raises(ValueError, match="scale_range"):
        GameConfig.model_validate({"scale_range": [1.2, 0.8]})


def test_game_steps_validate_and_normalize() -> None:
    spec = TestSpec.model_validate(
        {
            "name": "game controls",
            "target": "game",
            "config": {"surface": "android", "device": "emulator-5554"},
            "steps": [
                {"click_image": {"image": "ok.png", "threshold": 0.9}},
                {"tap": {"x": 0.5, "y": 0.8}},
                {"press_key": "space"},
                {"measure_fps": {"seconds": 1, "min_fps": 30}},
            ],
        }
    )
    assert [step.action for step in spec.steps] == [
        "click_image",
        "tap",
        "press_key",
        "measure_fps",
    ]


def test_click_image_uses_match_center_and_runner_maps_step(
    tmp_path: Path,
) -> None:
    scene, template = _scene()
    assert cv2.imwrite(str(tmp_path / "start.png"), template)
    driver, surface = _driver(tmp_path, [scene])

    _execute_step(
        driver,
        Step.model_validate({"click_image": "start.png"}),
        tmp_path,
        1,
    )

    assert surface.actions == [("click", 81, 39)]
    assert driver.metadata["matches"][0]["confidence"] > 0.99


def test_failed_match_saves_best_candidate_and_template(
    tmp_path: Path,
) -> None:
    scene = np.zeros((100, 160, 3), dtype=np.uint8)
    _, template = _scene()
    assert cv2.imwrite(str(tmp_path / "missing.png"), template)
    driver, _ = _driver(tmp_path, [scene])

    with pytest.raises(LookupError, match="Annotated candidate"):
        driver.click_image({"image": "missing.png", "threshold": 0.99})

    failure = driver.metadata["match_failures"][0]
    assert failure["best_confidence"] is not None
    assert Path(failure["annotated"]).is_file()
    assert Path(failure["template"]).is_file()


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ([0.5, 0.8], (80, 80)),
        ({"x": 0.5, "y": 0.8}, (80, 80)),
        ([40, 30], (40, 30)),
        ([1, 1], (159, 99)),
    ],
)
def test_tap_relative_and_absolute_coordinates(
    tmp_path: Path,
    point: list[float] | dict[str, float],
    expected: tuple[int, int],
) -> None:
    driver, surface = _driver(tmp_path, [np.zeros((100, 160, 3), dtype=np.uint8)])

    assert driver.tap(point) == expected
    assert surface.actions == [("click", *expected)]


def test_drag_resolves_relative_coordinates_and_press_key(tmp_path: Path) -> None:
    driver, surface = _driver(tmp_path, [np.zeros((100, 160, 3), dtype=np.uint8)])

    driver.drag({"from": [0.1, 0.2], "to": [100, 50], "duration": 0.25})
    driver.press_key("space")

    assert surface.actions == [
        ("drag", 16, 20, 100, 50, 0.25),
        ("press", "space"),
    ]


def test_wait_for_change_observes_next_frame(tmp_path: Path) -> None:
    first = np.zeros((60, 60, 3), dtype=np.uint8)
    second = first.copy()
    cv2.rectangle(second, (10, 10), (40, 40), (255, 255, 255), -1)
    driver, _ = _driver(tmp_path, [first, second])

    difference = driver.wait_for_screen_change({"timeout": 0.5})

    assert difference > 0.01

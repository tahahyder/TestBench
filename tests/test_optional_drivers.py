from pathlib import Path

import cv2
import numpy as np
import pytest

from testbench.ai.assist import is_enabled, text_to_yaml_steps
from testbench.drivers.base import Driver
from testbench.drivers.game import GameDriver
from testbench.drivers.mobile import MobileDriver
from testbench.drivers.web import WebDriver


@pytest.mark.parametrize("driver_type", [WebDriver, MobileDriver, GameDriver])
def test_target_drivers_implement_shared_interface(driver_type: type[Driver]) -> None:
    assert issubclass(driver_type, Driver)


def test_game_driver_matches_reference_image_without_desktop(tmp_path: Path) -> None:
    reference = np.random.default_rng(42).integers(0, 256, size=(5, 6, 3), dtype=np.uint8)
    screen = np.zeros((30, 40, 3), dtype=np.uint8)
    screen[10:15, 20:26] = reference
    image_path = tmp_path / "target.png"
    assert cv2.imwrite(str(image_path), reference)

    driver = GameDriver({"image_match_threshold": 0.99}, tmp_path / "artifacts")
    driver._capture = lambda: screen
    driver._monitor = {"left": 100, "top": 200, "width": 40, "height": 30}

    assert driver._locate(image_path) == (123, 212)


def test_ai_helper_is_disabled_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert not is_enabled()
    with pytest.raises(RuntimeError, match="AI assistance is disabled"):
        text_to_yaml_steps("Open a page")

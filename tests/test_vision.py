import cv2
import numpy as np
import pytest

from testbench.drivers.vision import (
    find_template,
    frame_difference,
    match_template,
    wait_for_change,
)


def synthetic_scene() -> tuple[np.ndarray, np.ndarray]:
    background = np.full((120, 180, 3), 25, dtype=np.uint8)
    cv2.rectangle(background, (80, 50), (109, 79), (40, 180, 245), -1)
    cv2.line(background, (84, 55), (105, 74), (250, 30, 30), 4)
    template = background[48:82, 78:112].copy()
    return background, template


def test_template_match_reports_center_box_and_confidence() -> None:
    scene, template = synthetic_scene()

    result = find_template(scene, template, threshold=0.99)

    assert result is not None
    assert result.confidence >= 0.99
    assert (result.x, result.y) == (95, 65)
    assert result.bbox == (78, 48, 34, 34)


def test_multiscale_match_finds_scaled_template() -> None:
    scene, template = synthetic_scene()
    scene = cv2.resize(scene, None, fx=1.2, fy=1.2, interpolation=cv2.INTER_NEAREST)

    result = find_template(scene, template, threshold=0.9, scales=[0.8, 1, 1.2])

    assert result is not None
    assert result.scale == 1.2
    assert result.x == 114
    assert result.y == 78


def test_region_limits_search_and_invalid_region_is_clear() -> None:
    scene, template = synthetic_scene()

    assert find_template(scene, template, threshold=0.9, region=(0, 0, 60, 100)) is None
    with pytest.raises(ValueError, match="inside the screenshot"):
        find_template(scene, template, region=(170, 110, 30, 20))


def test_multiple_matches_returns_separate_candidates() -> None:
    _, template = synthetic_scene()
    scene = np.full((100, 180, 3), 25, dtype=np.uint8)
    scene[10:44, 10:44] = template
    scene[50:84, 100:134] = template

    matches = match_template(scene, template, threshold=0.99, max_matches=5)

    assert len(matches) == 2
    assert {(match.x, match.y) for match in matches} == {(27, 27), (117, 67)}


def test_no_match_returns_none() -> None:
    scene = np.zeros((60, 60, 3), dtype=np.uint8)
    template = np.zeros((12, 12, 3), dtype=np.uint8)
    cv2.line(template, (1, 1), (10, 10), (255, 255, 255), 2)

    assert find_template(scene, template, threshold=0.95) is None


def test_constant_template_has_clear_detail_error() -> None:
    scene = np.zeros((30, 30, 3), dtype=np.uint8)
    template = np.full((5, 5, 3), 255, dtype=np.uint8)

    with pytest.raises(ValueError, match="too little visual detail"):
        find_template(scene, template)


def test_frame_difference_and_change_polling() -> None:
    before = np.zeros((24, 24, 3), dtype=np.uint8)
    after = before.copy()
    after[8:16, 8:16] = 255
    assert frame_difference(before, after) > 0
    frames = iter([before, before, after])

    changed, latest, difference = wait_for_change(
        lambda: next(frames),
        timeout=1,
        interval=0.001,
        threshold=0.1,
    )

    assert changed
    assert latest is after or np.array_equal(latest, after)
    assert difference > 0.1

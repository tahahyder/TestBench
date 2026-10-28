"""Template matching, polling, OCR, and frame-difference helpers."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Match:
    """Location and confidence of one screenshot/template match."""

    confidence: float
    x: int
    y: int
    bbox: tuple[int, int, int, int]
    scale: float


def match_template(
    screenshot: np.ndarray,
    template: np.ndarray,
    *,
    threshold: float = 0.85,
    scales: tuple[float, ...] | list[float] = (1.0,),
    region: tuple[int, int, int, int] | None = None,
    color_check: bool = False,
    max_matches: int = 1,
) -> list[Match]:
    """Return high-confidence multi-scale matches in descending confidence order."""
    if not -1 <= threshold <= 1:
        raise ValueError("match threshold must be between -1 and 1.")
    if max_matches < 1:
        raise ValueError("max_matches must be at least one.")
    screen = _to_bgr(screenshot)
    template_bgr = _to_bgr(template)
    height, width = screen.shape[:2]
    left, top, region_width, region_height = _validate_region(region, width, height)
    screen_roi = screen[top : top + region_height, left : left + region_width]
    gray_roi = cv2.cvtColor(screen_roi, cv2.COLOR_BGR2GRAY)
    gray_template = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    if float(np.std(gray_template)) < 1:
        raise ValueError(
            "Template has too little visual detail for normalized matching; "
            "crop a tighter region that includes edges or text."
        )
    candidates: list[Match] = []

    for scale in scales:
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError("Every scale value must be a positive finite number.")
        scaled_width = max(1, round(gray_template.shape[1] * scale))
        scaled_height = max(1, round(gray_template.shape[0] * scale))
        if scaled_width > region_width or scaled_height > region_height:
            continue
        scaled_gray = cv2.resize(
            gray_template,
            (scaled_width, scaled_height),
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
        )
        response = cv2.matchTemplate(gray_roi, scaled_gray, cv2.TM_CCOEFF_NORMED)
        for _ in range(max_matches):
            _, confidence, _, location = cv2.minMaxLoc(response)
            if not np.isfinite(confidence) or confidence < threshold:
                break
            x, y = left + location[0], top + location[1]
            if color_check:
                screen_crop = screen[y : y + scaled_height, x : x + scaled_width]
                template_crop = cv2.resize(template_bgr, (scaled_width, scaled_height))
                if _color_distance(screen_crop, template_crop) > 90:
                    response[
                        location[1] : location[1] + scaled_height,
                        location[0] : location[0] + scaled_width,
                    ] = -1
                    continue
            candidates.append(
                Match(
                    confidence=float(confidence),
                    x=x + scaled_width // 2,
                    y=y + scaled_height // 2,
                    bbox=(x, y, scaled_width, scaled_height),
                    scale=float(scale),
                )
            )
            x0, y0 = location
            response[
                y0 : y0 + scaled_height,
                x0 : x0 + scaled_width,
            ] = -1

    candidates.sort(key=lambda result: result.confidence, reverse=True)
    return _suppress_overlaps(candidates, max_matches)


def find_template(
    screenshot: np.ndarray,
    template: np.ndarray,
    *,
    threshold: float = 0.85,
    scales: tuple[float, ...] | list[float] = (1.0,),
    region: tuple[int, int, int, int] | None = None,
    color_check: bool = False,
) -> Match | None:
    """Return the best qualifying template match, if one exists."""
    matches = match_template(
        screenshot,
        template,
        threshold=threshold,
        scales=scales,
        region=region,
        color_check=color_check,
    )
    return matches[0] if matches else None


def wait_for_template(
    capture: Callable[[], np.ndarray],
    template: np.ndarray,
    *,
    timeout: float,
    interval: float = 0.2,
    threshold: float = 0.85,
    scales: tuple[float, ...] | list[float] = (1.0,),
    region: tuple[int, int, int, int] | None = None,
    color_check: bool = False,
) -> tuple[Match | None, np.ndarray]:
    """Poll a capture source until a template is found or timeout expires."""
    if timeout < 0:
        raise ValueError("timeout must not be negative.")
    if interval <= 0:
        raise ValueError("poll interval must be greater than zero.")
    deadline = time.monotonic() + timeout
    latest = capture()
    while True:
        found = find_template(
            latest,
            template,
            threshold=threshold,
            scales=scales,
            region=region,
            color_check=color_check,
        )
        if found is not None:
            return found, latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, latest
        time.sleep(min(interval, remaining))
        latest = capture()


def annotate_match(
    screenshot: np.ndarray,
    template: np.ndarray,
    match: Match | None,
    path: Path,
) -> Path:
    """Save an annotated screenshot and template debug panel."""
    frame = _to_bgr(screenshot).copy()
    panel_width = min(
        frame.shape[1],
        max(1, min(max(180, template.shape[1]), max(180, frame.shape[1] // 2))),
    )
    panel_height = min(frame.shape[0], max(1, max(44, template.shape[0] + 36)))
    panel = np.full((panel_height, panel_width, 3), 32, dtype=np.uint8)
    preview_w = min(template.shape[1], max(1, panel_width - 12))
    preview_h = min(template.shape[0], max(1, panel_height - 34))
    resized = cv2.resize(template, (preview_w, preview_h))
    panel[6 : 6 + preview_h, 6 : 6 + preview_w] = resized
    if match is None:
        cv2.putText(
            panel,
            "NO MATCH",
            (6, panel_height - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
        )
    else:
        x, y, width, height = match.bbox
        cv2.rectangle(frame, (x, y), (x + width, y + height), (0, 0, 255), 2)
        cv2.putText(
            frame,
            f"{match.confidence:.3f}",
            (x, max(18, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
        )
        cv2.putText(
            panel,
            f"{match.confidence:.3f} scale={match.scale:g}",
            (6, panel_height - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 0),
            1,
        )
    cv2.rectangle(
        frame,
        (0, 0),
        (panel_width - 1, panel_height - 1),
        (32, 32, 32),
        -1,
    )
    frame[0:panel_height, 0:panel_width] = panel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), frame):
        raise OSError(f"Could not write annotated screenshot: {path}")
    return path


def read_text(screenshot: np.ndarray) -> list[str]:
    """Read visible text with pytesseract or EasyOCR, if installed."""
    prepared = _preprocess_for_ocr(screenshot)
    try:
        import pytesseract
    except ImportError:
        try:
            import easyocr
        except ImportError as exc:
            raise RuntimeError(
                "OCR needs an installed engine. Install pytesseract and the "
                "Tesseract executable (`python -m pip install pytesseract`, then "
                "`winget install UB-Mannheim.TesseractOCR`), or run "
                "`python -m pip install easyocr`."
            ) from exc
        reader = easyocr.Reader(["en"], gpu=False)
        return [text for _, text, confidence in reader.readtext(prepared) if confidence > 0.2]
    text = pytesseract.image_to_string(prepared)
    return [line.strip() for line in text.splitlines() if line.strip()]


def frame_difference(
    before: np.ndarray,
    after: np.ndarray,
    *,
    region: tuple[int, int, int, int] | None = None,
) -> float:
    """Return normalized grayscale mean absolute pixel difference."""
    first = _to_bgr(before)
    second = _to_bgr(after)
    if first.shape != second.shape:
        second = cv2.resize(second, (first.shape[1], first.shape[0]))
    height, width = first.shape[:2]
    left, top, region_width, region_height = _validate_region(region, width, height)
    first_gray = cv2.cvtColor(
        first[top : top + region_height, left : left + region_width],
        cv2.COLOR_BGR2GRAY,
    )
    second_gray = cv2.cvtColor(
        second[top : top + region_height, left : left + region_width],
        cv2.COLOR_BGR2GRAY,
    )
    difference = cv2.absdiff(first_gray, second_gray)
    return float(np.mean(difference) / 255)


def wait_for_change(
    capture: Callable[[], np.ndarray],
    *,
    timeout: float,
    interval: float = 0.2,
    region: tuple[int, int, int, int] | None = None,
    threshold: float = 0.01,
) -> tuple[bool, np.ndarray, float]:
    """Poll frames until their difference exceeds threshold or time runs out."""
    if timeout < 0:
        raise ValueError("timeout must not be negative.")
    before = capture()
    latest = before
    deadline = time.monotonic() + timeout
    while True:
        latest = capture()
        difference = frame_difference(before, latest, region=region)
        if difference >= threshold:
            return True, latest, difference
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, latest, difference
        time.sleep(min(interval, remaining))


def _preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(_to_bgr(image), cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(
        gray,
        None,
        fx=2,
        fy=2,
        interpolation=cv2.INTER_CUBIC,
    )
    return cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def _validate_region(region: tuple[int, int, int, int] | None, width: int, height: int) -> tuple[int, int, int, int]:
    if region is None:
        return 0, 0, width, height
    if len(region) != 4:
        raise ValueError("region must be [x, y, width, height].")
    left, top, region_width, region_height = region
    if (
        min(left, top) < 0
        or region_width <= 0
        or region_height <= 0
        or left + region_width > width
        or top + region_height > height
    ):
        raise ValueError("region must be a positive rectangle inside the screenshot.")
    return left, top, region_width, region_height


def _to_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image.shape[2] == 3:
        return image
    raise ValueError("Images must have 1, 3, or 4 channels.")


def _color_distance(first: np.ndarray, second: np.ndarray) -> float:
    if first.size == 0:
        return float("inf")
    first_hsv = cv2.cvtColor(first, cv2.COLOR_BGR2HSV)
    second_hsv = cv2.cvtColor(second, cv2.COLOR_BGR2HSV)
    return float(np.mean(cv2.absdiff(first_hsv, second_hsv)))


def _suppress_overlaps(matches: list[Match], limit: int) -> list[Match]:
    selected: list[Match] = []
    for candidate in matches:
        x, y, width, height = candidate.bbox
        overlaps = False
        for previous in selected:
            px, py, pwidth, pheight = previous.bbox
            intersection_w = max(0, min(x + width, px + pwidth) - max(x, px))
            intersection_h = max(0, min(y + height, py + pheight) - max(y, py))
            if intersection_w * intersection_h > 0:
                overlaps = True
                break
        if not overlaps:
            selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected

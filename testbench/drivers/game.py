"""Vision-based game driver using pluggable screenshot/input surfaces."""

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from testbench.core.steps import GameConfig
from testbench.drivers.base import Driver
from testbench.drivers.surfaces.android import AndroidSurface
from testbench.drivers.surfaces.base import Surface
from testbench.drivers.surfaces.browser import BrowserSurface
from testbench.drivers.surfaces.desktop import DesktopSurface
from testbench.drivers.vision import (
    Match,
    annotate_match,
    find_template,
    frame_difference,
    match_template,
    read_text,
    wait_for_change,
    wait_for_template,
)


class GameDriver(Driver):
    """Run image, OCR, coordinate, and stability steps on a game surface."""

    def __init__(
        self,
        config: dict[str, Any],
        artifact_dir: Path,
        *,
        surface: Surface | None = None,
        recording: bool = True,
    ) -> None:
        super().__init__(config, artifact_dir)
        try:
            self.settings = GameConfig.model_validate(config)
        except ValueError as exc:
            raise ValueError(f"Invalid game config: {exc}") from exc
        self.surface = surface
        self._recording_enabled = recording and surface is None
        self._video_writer: cv2.VideoWriter | None = None
        self._closed = False
        self.metadata: dict[str, Any] = {
            "surface": self.settings.surface,
            "matches": [],
            "fps": [],
            "ai_fallback_uses": [],
        }
        self._monitor: dict[str, int] | None = None

    @property
    def _match_threshold(self) -> float:
        return (
            self.settings.image_match_threshold
            if self.settings.image_match_threshold is not None
            else self.settings.match_threshold
        )

    def _locate(self, image_path: Path) -> tuple[int, int]:
        """Locate an image and return desktop absolute coordinates."""
        match = find_template(
            self._capture(),
            self._load_template(image_path),
            threshold=self._match_threshold,
            scales=self._scales(),
        )
        if match is None:
            return (-1, -1)
        if self._monitor is not None:
            return match.x + self._monitor["left"], match.y + self._monitor["top"]
        return match.x, match.y

    def open(self, destination: str) -> None:
        if self.surface is None:
            self.surface = _make_surface(self.settings, self.artifact_dir)
        if isinstance(self.surface, BrowserSurface):
            self.surface.open(destination or self.settings.url or "")
        elif isinstance(self.surface, DesktopSurface):
            self.surface.open()
        else:
            opener = getattr(self.surface, "open", None)
            if callable(opener):
                opener()
        width, height = self.surface.size()
        self.metadata.update(
            {
                "surface": self.settings.surface,
                "resolution": f"{width}x{height}",
                "width": width,
                "height": height,
            }
        )
        self.metadata.update(getattr(self.surface, "metadata", {}))
        if self.settings.surface != "android":
            self._capture()

    def click(self, target: str | dict[str, str]) -> None:
        if isinstance(target, dict):
            raise ValueError("Game clicks use image matching, OCR text, or tap coordinates.")
        if Path(target).suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
            self.click_image(target)
        else:
            self.click_text(target)

    def press_key(self, key: str) -> None:
        self._require_surface().press(key)

    def type(self, field: str, value: str) -> None:
        self.click_text(field)
        type_text = getattr(self._require_surface(), "type_text", None)
        if not callable(type_text):
            raise RuntimeError("The configured game surface cannot type text.")
        type_text(value)

    def wait(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("wait duration must not be negative.")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.2, deadline - time.monotonic()))

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), self._capture()):
            raise OSError(f"Could not write game screenshot to {path}.")

    def expect_visible(self, target: str) -> None:
        if Path(target).suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
            self._match_image(target, wait=True)
            return
        self.expect_text(target)

    def expect_text(self, text: str) -> None:
        if self._contains_text(text):
            return
        deadline = time.monotonic() + self.settings.step_timeout
        while time.monotonic() < deadline:
            self.wait(min(0.2, deadline - time.monotonic()))
            if self._contains_text(text):
                return
        raise AssertionError(f"Expected game text {text!r} within {self.settings.step_timeout:g}s.")

    def wait_for_image(self, value: str | dict[str, Any]) -> Match:
        template_path, threshold, region, _ = self._image_options(value)
        template = self._load_template(template_path)
        match, frame = wait_for_template(
            self._capture,
            template,
            timeout=self.settings.step_timeout,
            interval=0.2,
            threshold=threshold,
            scales=self._scales(),
            region=region,
            color_check=self.settings.color_check,
        )
        if match is None:
            self._save_match_debug(template_path, frame, threshold, region)
            raise LookupError(
                f"Image {template_path.name!r} was not found within "
                f"{self.settings.step_timeout:g}s at confidence {threshold:.2f}."
            )
        annotated = self._save_success_annotation(
            template_path,
            frame,
            match,
            "matched",
        )
        self.metadata["matches"].append(
            {
                "image": template_path.name,
                "confidence": match.confidence,
                "x": match.x,
                "y": match.y,
                "annotated": str(annotated),
                "used_ai_fallback": False,
            }
        )
        return match

    def click_image(self, value: str | dict[str, Any]) -> Match:
        template_path, threshold, region, description = self._image_options(value)
        try:
            match, frame = wait_for_template(
                self._capture,
                self._load_template(template_path),
                timeout=self.settings.step_timeout,
                interval=0.2,
                threshold=threshold,
                scales=self._scales(),
                region=region,
                color_check=self.settings.color_check,
            )
        except FileNotFoundError:
            raise
        if match is None:
            debug_path = self._save_match_debug(
                template_path,
                frame,
                threshold,
                region,
            )
            fallback = self._try_ai_fallback(frame, description)
            if fallback is None:
                raise LookupError(
                    f"Image {template_path.name!r} was not found at confidence "
                    f"{threshold:.2f}. Annotated candidate: {debug_path}"
                )
            x, y = fallback
            width, height = self._require_surface().size()
            match = Match(
                confidence=0.0,
                x=x,
                y=y,
                bbox=(x, y, 1, 1),
                scale=1.0,
            )
            self.metadata["ai_fallback_uses"].append({"description": description or template_path.name, "x": x, "y": y})
        surface = self._require_surface()
        surface.click(match.x, match.y)
        annotated = self._save_success_annotation(template_path, frame, match)
        self.metadata["matches"].append(
            {
                "image": template_path.name,
                "confidence": match.confidence,
                "x": match.x,
                "y": match.y,
                "annotated": str(annotated),
                "used_ai_fallback": match.confidence == 0,
            }
        )
        return match

    def expect_image(self, value: str | dict[str, Any]) -> Match:
        return self.wait_for_image(value)

    def expect_no_image(self, value: str | dict[str, Any]) -> None:
        template_path, threshold, region, _ = self._image_options(value)
        frame = self._capture()
        template = self._load_template(template_path)
        match = find_template(
            frame,
            template,
            threshold=threshold,
            scales=self._scales(),
            region=region,
            color_check=self.settings.color_check,
        )
        if match:
            annotated = self._save_success_annotation(
                template_path,
                frame,
                match,
                "unexpected",
            )
            self.metadata["matches"].append(
                {
                    "image": template_path.name,
                    "confidence": match.confidence,
                    "annotated": str(annotated),
                    "expected_absent": True,
                }
            )
            raise AssertionError(f"Image {template_path.name!r} was visible at {match.confidence:.2f} confidence.")

    def click_text(self, text: str) -> None:
        surface = self._require_surface()
        frame = self._capture()
        locator = getattr(surface, "locate_text", None)
        point = locator(text) if callable(locator) else None
        if point is None:
            point = _find_ocr_text(frame, text)
        if point is None:
            raise LookupError(
                f"OCR could not find text {text!r}. Install pytesseract and the "
                "Tesseract executable, or install EasyOCR."
            )
        surface.click(*point)
        annotation = self._save_point_annotation(frame, *point, text)
        self.metadata.setdefault("text_clicks", []).append(
            {"text": text, "x": point[0], "y": point[1], "annotated": str(annotation)}
        )

    def tap(self, point: list[float] | tuple[float, float] | dict[str, float]) -> tuple[int, int]:
        width, height = self._require_surface().size()
        if isinstance(point, dict):
            point = (point["x"], point["y"])
        x, y = point
        relative = x <= 1 and y <= 1
        resolved = (round(x * width), round(y * height)) if relative else (round(x), round(y))
        if relative:
            resolved = (min(resolved[0], width - 1), min(resolved[1], height - 1))
        if min(resolved) < 0:
            raise ValueError("Tap coordinates must not be negative.")
        if resolved[0] >= width or resolved[1] >= height:
            raise ValueError(f"Tap coordinate {resolved} is outside the {width}x{height} surface.")
        self._require_surface().click(*resolved)
        return resolved

    def drag(self, value: dict[str, Any]) -> None:
        width, height = self._require_surface().size()
        start = _resolve_point(value["from"], width, height)
        end = _resolve_point(value["to"], width, height)
        self._require_surface().drag(
            *start,
            *end,
            float(value.get("duration", 0.5)),
        )

    def hold_key(self, key: str, seconds: float) -> None:
        surface = self._require_surface()
        surface.key_down(key)
        try:
            self.wait(seconds)
        finally:
            surface.key_up(key)

    def wait_for_screen_change(
        self,
        value: dict[str, Any],
    ) -> float:
        changed, frame, difference = wait_for_change(
            self._capture,
            timeout=float(value["timeout"]),
            region=_region_tuple(value.get("region")),
            threshold=0.001,
        )
        if not changed:
            self.screenshot(self.artifact_dir / "no_screen_change.png")
            raise TimeoutError(f"Game screen did not change within {value['timeout']} seconds.")
        self.metadata["last_frame_difference"] = difference
        return difference

    def wait_for_change(self, value: dict[str, Any]) -> float:
        return self.wait_for_screen_change(value)

    def expect_no_change(self, value: dict[str, Any]) -> float:
        duration = float(value["seconds"])
        before = self._capture()
        deadline = time.monotonic() + duration
        maximum_difference = 0.0
        while time.monotonic() < deadline:
            frame = self._capture()
            maximum_difference = max(
                maximum_difference,
                frame_difference(before, frame, region=_region_tuple(value.get("region"))),
            )
            if maximum_difference >= 0.001:
                break
            self.wait(min(0.2, max(0, deadline - time.monotonic())))
        if maximum_difference < 0.001:
            raise AssertionError(f"Game screen did not change for {duration:g}s; possible freeze.")
        return maximum_difference

    def measure_fps(self, seconds: float, min_fps: float) -> float:
        captures = 0
        started = time.monotonic()
        deadline = started + seconds
        while time.monotonic() < deadline:
            self._capture()
            captures += 1
        elapsed = time.monotonic() - started
        fps = captures / elapsed if elapsed else 0.0
        measurement = {
            "seconds": seconds,
            "frames": captures,
            "fps": round(fps, 2),
            "min_fps": min_fps,
            "passed": fps >= min_fps,
        }
        self.metadata["fps"].append(measurement)
        if fps < min_fps:
            raise AssertionError(f"Measured {fps:.2f} FPS, below required {min_fps:.2f} FPS.")
        return fps

    def match_now(self, image_name: str, threshold: float | None = None) -> dict[str, Any]:
        template_path = self._template_path(image_name)
        frame = self._capture()
        match = find_template(
            frame,
            self._load_template(template_path),
            threshold=self._match_threshold if threshold is None else threshold,
            scales=self._scales(),
            color_check=self.settings.color_check,
        )
        if match is None:
            return {"found": False, "confidence": 0.0}
        return {
            "found": True,
            "confidence": match.confidence,
            "x": match.x,
            "y": match.y,
            "bbox": list(match.bbox),
        }

    def close(self) -> None:
        error: Exception | None = None
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None
        if self.surface is not None:
            try:
                self.surface.close()
            except Exception as exc:
                error = exc
        self._closed = True
        if error is not None:
            raise error

    def _capture(self) -> np.ndarray:
        frame = self._require_surface().capture()
        if frame.ndim != 3 or frame.shape[2] not in {3, 4}:
            raise ValueError("Game surface capture must be a color image.")
        if self._recording_enabled and self.settings.surface != "android":
            self._record_frame(frame)
        return frame

    def _record_frame(self, frame: np.ndarray) -> None:
        if self._video_writer is None:
            height, width = frame.shape[:2]
            output = self.artifact_dir / "game_recording.mp4"
            writer = cv2.VideoWriter(
                str(output),
                cv2.VideoWriter_fourcc(*"mp4v"),
                15,
                (width, height),
            )
            if not writer.isOpened():
                raise OSError(f"OpenCV could not start game recording at {output}.")
            self._video_writer = writer
        if self._video_writer is not None:
            self._video_writer.write(frame[:, :, :3])

    def _match_image(
        self,
        value: str | dict[str, Any],
        *,
        wait: bool,
    ) -> Match | None:
        path, threshold, region, _ = self._image_options(value)
        template = self._load_template(path)
        if wait:
            match, frame = wait_for_template(
                self._capture,
                template,
                timeout=self.settings.step_timeout,
                interval=0.2,
                threshold=threshold,
                scales=self._scales(),
                region=region,
                color_check=self.settings.color_check,
            )
        else:
            frame = self._capture()
            match = find_template(
                frame,
                template,
                threshold=threshold,
                scales=self._scales(),
                region=region,
                color_check=self.settings.color_check,
            )
        if match is None:
            debug = self._save_match_debug(path, frame, threshold, region)
            raise LookupError(
                f"Image {path.name!r} was not found at confidence {threshold:.2f}. Annotated candidate: {debug}"
            )
        self.metadata["matches"].append({"image": path.name, "confidence": match.confidence})
        return match

    def _image_options(
        self, value: str | dict[str, Any]
    ) -> tuple[Path, float, tuple[int, int, int, int] | None, str | None]:
        if isinstance(value, str):
            image = value
            threshold = self._match_threshold
            region = None
            description = None
        else:
            image = value["image"]
            threshold = float(value.get("threshold", self._match_threshold))
            region = _region_tuple(value.get("region"))
            description = value.get("describe")
        return self._template_path(image), threshold, region, description

    def _template_path(self, name: str) -> Path:
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = Path(self.settings.templates_dir).expanduser() / path
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Game template image was not found: {resolved}")
        return resolved

    @staticmethod
    def _load_template(path: Path) -> np.ndarray:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not read game template image: {path}")
        return image

    def _scales(self) -> tuple[float, ...]:
        low, high = self.settings.scale_range
        values = np.arange(low, high + 0.001, 0.1)
        return tuple(sorted({round(float(value), 2) for value in values} | {1.0}))

    def _save_match_debug(
        self,
        template_path: Path,
        frame: np.ndarray,
        threshold: float,
        region: tuple[int, int, int, int] | None,
    ) -> Path:
        template = self._load_template(template_path)
        candidates = match_template(
            frame,
            template,
            threshold=-1,
            scales=self._scales(),
            region=region,
        )
        best = candidates[0] if candidates else None
        safe_name = _safe_stem(template_path.stem)
        destination = self.artifact_dir / "debug" / f"match_failed_{safe_name}.png"
        annotated = annotate_match(frame, template, best, destination)
        template_copy = destination.with_name(f"template_{safe_name}{template_path.suffix}")
        if not cv2.imwrite(str(template_copy), template):
            raise OSError(f"Could not write template debug image: {template_copy}")
        self.metadata.setdefault("match_failures", []).append(
            {
                "image": template_path.name,
                "threshold": threshold,
                "best_confidence": best.confidence if best else None,
                "annotated": str(annotated),
                "template": str(template_copy),
            }
        )
        return annotated

    def _save_success_annotation(
        self,
        template_path: Path,
        frame: np.ndarray,
        match: Match,
        label: str = "clicked",
    ) -> Path:
        destination = (
            self.artifact_dir
            / "annotated"
            / f"{label}_{_safe_stem(template_path.stem)}_{len(self.metadata['matches']) + 1}.png"
        )
        return annotate_match(
            frame,
            self._load_template(template_path),
            match,
            destination,
        )

    def _save_point_annotation(
        self,
        frame: np.ndarray,
        x: int,
        y: int,
        label: str,
    ) -> Path:
        annotated = frame.copy()
        cv2.circle(annotated, (x, y), 13, (0, 255, 0), 3)
        cv2.putText(
            annotated,
            label[:60],
            (min(x + 16, max(0, annotated.shape[1] - 100)), max(18, y - 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
        )
        destination = (
            self.artifact_dir
            / "annotated"
            / (f"text_{_safe_stem(label)}_{len(self.metadata.get('text_clicks', [])) + 1}.png")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(destination), annotated):
            raise OSError(f"Could not write annotated text click: {destination}")
        return destination

    def _contains_text(self, expected: str) -> bool:
        surface = self._require_surface()
        read_browser_text = getattr(surface, "read_visible_text", None)
        if callable(read_browser_text):
            if any(expected.casefold() in line.casefold() for line in read_browser_text()):
                return True
        return any(expected.casefold() in line.casefold() for line in read_text(self._capture()))

    def _try_ai_fallback(self, frame: np.ndarray, description: str | None) -> tuple[int, int] | None:
        if not self.settings.ai_fallback or not description:
            return None
        from testbench.ai.vision_assist import is_enabled, locate_description

        if not is_enabled():
            self.metadata["ai_fallback_status"] = "disabled because AI is off or provider consent is missing"
            return None

        from testbench.ai.client import AIClient
        from testbench.ai.config import load_ai_config

        ai_config = load_ai_config()
        ai_config["model"] = ai_config.get("vision_model", ai_config.get("model"))
        point = locate_description(
            frame,
            description,
            client=AIClient(ai_config),
            run_id=self.artifact_dir.name,
        )
        if point is None:
            return None
        width, height = self._require_surface().size()
        x = min(max(int(point[0]), 0), width - 1)
        y = min(max(int(point[1]), 0), height - 1)
        return x, y

    def _require_surface(self) -> Surface:
        if self.surface is None:
            raise RuntimeError("Open the game surface before using the game driver.")
        return self.surface


def _make_surface(config: GameConfig, artifact_dir: Path) -> Surface:
    if config.surface == "desktop":
        surface = DesktopSurface(config.window_title)
        return surface
    if config.surface == "browser":
        return BrowserSurface()
    return AndroidSurface(config.device, artifact_dir)


def _region_tuple(value: Any) -> tuple[int, int, int, int] | None:
    return tuple(int(part) for part in value) if value is not None else None


def _resolve_point(
    point: list[float] | tuple[float, float],
    width: int,
    height: int,
) -> tuple[int, int]:
    x, y = point
    relative = x <= 1 and y <= 1
    resolved = (round(x * width), round(y * height)) if relative else (round(x), round(y))
    if relative:
        resolved = (min(resolved[0], width - 1), min(resolved[1], height - 1))
    if min(resolved) < 0:
        raise ValueError("Point coordinates must not be negative.")
    if resolved[0] >= width or resolved[1] >= height:
        raise ValueError(f"Point {resolved} is outside the {width}x{height} surface.")
    return resolved


def _find_ocr_text(frame: np.ndarray, expected: str) -> tuple[int, int] | None:
    try:
        import pytesseract
    except ImportError:
        try:
            import easyocr
        except ImportError as exc:
            raise RuntimeError(
                "OCR needs an installed engine. Install pytesseract and Tesseract "
                "(`python -m pip install pytesseract`, then `winget install "
                "UB-Mannheim.TesseractOCR`), or run "
                "`python -m pip install easyocr`."
            ) from exc
        reader = easyocr.Reader(["en"], gpu=False)
        results = reader.readtext(frame)
        for box, text, confidence in results:
            if confidence >= 0.2 and expected.casefold() in text.casefold():
                return (
                    round(sum(point[0] for point in box) / len(box)),
                    round(sum(point[1] for point in box) / len(box)),
                )
        return None

    data = pytesseract.image_to_data(
        frame,
        output_type=pytesseract.Output.DICT,
        config="--psm 11",
    )
    for index, text in enumerate(data["text"]):
        if expected.casefold() in text.strip().casefold():
            return (
                int(data["left"][index]) + int(data["width"][index]) // 2,
                int(data["top"][index]) + int(data["height"][index]) // 2,
            )
    return None


def _safe_stem(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)

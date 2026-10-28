"""Playwright surface for HTML5 and canvas games."""

import cv2
import numpy as np
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from testbench.drivers.surfaces.base import Surface


class BrowserSurface(Surface):
    """Capture a browser viewport and send mouse/keyboard events to it."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    def open(self, url: str, *, headless: bool = True) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._page = self._browser.new_page(viewport={"width": 960, "height": 640})
        self._page.goto(url, wait_until="domcontentloaded")

    def capture(self) -> np.ndarray:
        page = self._require_page()
        data = page.screenshot(full_page=False)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError("Playwright returned an unreadable game screenshot.")
        return image

    def click(self, x: int, y: int) -> None:
        self._require_page().mouse.click(x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float) -> None:
        page = self._require_page()
        page.mouse.move(x1, y1)
        page.mouse.down()
        page.mouse.move(x2, y2, steps=max(1, round(duration * 30)))
        page.mouse.up()

    def key_down(self, key: str) -> None:
        self._require_page().keyboard.down(_playwright_key(key))

    def key_up(self, key: str) -> None:
        self._require_page().keyboard.up(_playwright_key(key))

    def press(self, key: str) -> None:
        self._require_page().keyboard.press(_playwright_key(key))

    def type_text(self, text: str) -> None:
        self._require_page().keyboard.type(text)

    def size(self) -> tuple[int, int]:
        dimensions = self._require_page().evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
        return int(dimensions["width"]), int(dimensions["height"])

    def read_visible_text(self) -> list[str]:
        """Return page-visible text for browser games without OCR dependencies."""
        page = self._require_page()
        return page.locator("body").inner_text().splitlines()

    def locate_text(self, text: str) -> tuple[int, int] | None:
        """Return the center of matching browser text, if it is visible."""
        locator = self._require_page().get_by_text(text, exact=True).first
        if locator.count() == 0 or not locator.is_visible():
            return None
        bounds = locator.bounding_box()
        if bounds is None:
            return None
        return (
            round(bounds["x"] + bounds["width"] / 2),
            round(bounds["y"] + bounds["height"] / 2),
        )

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Open the browser game before using its surface.")
        return self._page


def _playwright_key(key: str) -> str:
    aliases = {
        "space": "Space",
        "left": "ArrowLeft",
        "right": "ArrowRight",
        "up": "ArrowUp",
        "down": "ArrowDown",
        "enter": "Enter",
        "escape": "Escape",
    }
    return aliases.get(key.lower(), key)

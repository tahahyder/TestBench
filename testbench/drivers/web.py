"""Playwright-backed web automation driver."""

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error,
    Locator,
    Page,
    Playwright,
    Video,
    sync_playwright,
)

from testbench.drivers.base import Driver


class WebDriver(Driver):
    """Web driver with semantic locator fallbacks and trace/video recording."""

    def __init__(self, config: dict[str, Any], artifact_dir: Path) -> None:
        super().__init__(config, artifact_dir)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._video: Video | None = None
        self.metadata: dict[str, Any] = {
            "console_errors": [],
            "network_errors": [],
        }

    def open(self, destination: str) -> None:
        browser_name = str(self.config.get("browser", "chromium"))
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"Unsupported browser: {browser_name}")

        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        video_dir = self.artifact_dir / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, browser_name)
        self._browser = browser_type.launch(headless=bool(self.config.get("headless", True)))
        self._context = self._browser.new_context(
            record_video_dir=str(video_dir),
        )
        self._context.set_default_timeout(int(self.config.get("timeout_ms", 10_000)))
        self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self._page = self._context.new_page()
        self._video = self._page.video
        self._page.on("console", self._record_console)
        self._page.on("response", self._record_response)
        self._page.goto(destination, wait_until="domcontentloaded")

    def click(self, target: str | dict[str, Any]) -> None:
        self._find_locator(target, for_input=False).click()

    def type(self, field: str | dict[str, Any], value: str) -> None:
        self._find_locator(field, for_input=True).fill(value)

    def select_option(self, field: str | dict[str, Any], value: str) -> None:
        self._find_locator(field, for_input=True).select_option(label=value)

    def scroll(self, movement: dict[str, Any]) -> None:
        direction = movement["direction"]
        amount = float(movement["amount"])
        sign = -1 if direction in {"up", "left"} else 1
        horizontal = sign * amount if direction in {"left", "right"} else 0
        vertical = sign * amount if direction in {"up", "down"} else 0
        self._require_page().mouse.wheel(horizontal, vertical)

    def expect_url_contains(self, text: str) -> None:
        if text not in self._require_page().url:
            raise AssertionError(f"Expected URL to contain {text!r}; got {self._require_page().url!r}.")

    def expect_enabled(self, target: str | dict[str, Any], enabled: bool) -> None:
        locator = self._find_locator(target, for_input=False)
        actual = locator.is_enabled()
        if actual != enabled:
            state = "enabled" if enabled else "disabled"
            raise AssertionError(f"Expected {target!r} to be {state}.")

    def wait(self, seconds: float) -> None:
        self._require_page().wait_for_timeout(seconds * 1000)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._require_page().screenshot(path=str(path), full_page=True)

    def expect_visible(self, target: str | dict[str, Any]) -> None:
        locator = self._find_locator(target, for_input=False)
        if not locator.is_visible():
            raise AssertionError(f"Expected {target!r} to be visible.")

    def expect_text(self, text: str) -> None:
        locator = self._find_locator(text, for_input=False)
        if not locator.is_visible():
            raise AssertionError(f"Expected text {text!r} to be visible.")

    def healing_candidates(self, target: str | dict[str, Any]) -> list[dict[str, Any]]:
        """Find visible, enabled text/label alternatives without using a model."""
        description, expected_role = _locator_description(target)
        if not description:
            return []
        page = self._require_page()
        try:
            nodes = page.locator("button, a, input, textarea, select, [role], label").evaluate_all(
                """nodes => nodes.map(node => {
                  const labels = node.labels ? [...node.labels].map(x => x.innerText).join(" ") : "";
                  return {
                    text: (node.innerText || node.value || "").trim(),
                    label: labels.trim(),
                    aria: node.getAttribute("aria-label") || "",
                    role: node.getAttribute("role") || (
                      node.tagName === "BUTTON" ? "button" :
                      node.tagName === "A" ? "link" :
                      node.tagName === "SELECT" ? "combobox" :
                      node.tagName === "TEXTAREA" ? "textbox" :
                      node.tagName === "INPUT" && ["checkbox","radio","submit","button"].includes(node.type)
                        ? node.type === "submit" || node.type === "button" ? "button" : node.type
                        : node.tagName === "INPUT" ? "textbox" : node.tagName.toLowerCase()
                    ),
                    disabled: Boolean(node.disabled || node.getAttribute("aria-disabled") === "true"),
                    visible: Boolean(node.getClientRects().length)
                  };
                })"""
            )
        except Error:
            return []
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in nodes:
            if not node["visible"] or node["disabled"]:
                continue
            if expected_role and node["role"].lower() != expected_role.lower():
                continue
            for kind, field in (("label", "label"), ("role", "aria"), ("text", "text")):
                candidate_text = node.get(field, "")
                if not candidate_text or candidate_text in seen:
                    continue
                confidence = SequenceMatcher(
                    None, description.casefold().strip(), candidate_text.casefold().strip()
                ).ratio()
                if confidence < 0.62:
                    continue
                locator = (
                    {"role": {"role": expected_role or node["role"], "name": candidate_text}}
                    if kind == "role" or (expected_role and kind == "text")
                    else {kind: candidate_text}
                )
                try:
                    found = self._find_locator(locator, for_input=False)
                    if found.count() != 1 or not found.is_visible() or not found.is_enabled():
                        continue
                except (Error, LookupError, ValueError):
                    continue
                seen.add(candidate_text)
                candidates.append(
                    {
                        "locator": locator,
                        "confidence": confidence,
                        "method": "fuzzy_text_label",
                        "explanation": f"Matched visible text {candidate_text!r}.",
                    }
                )
        return sorted(candidates, key=lambda item: item["confidence"], reverse=True)

    def healing_snapshot(self) -> dict[str, Any]:
        """Return a compact current-page snapshot for consented AI healing."""
        page = self._require_page()
        try:
            accessibility = page.locator("body").aria_snapshot()
        except Error:
            accessibility = ""
        fields = page.locator("input,textarea,select,button").evaluate_all(
            """nodes => nodes.map(node => {
              const labels = node.labels ? [...node.labels].map(x => x.innerText).join(" ") : "";
              const sensitive = node.type === "password" || /password|token|secret|otp|cvv|card/i.test(
                [node.name, node.id, node.placeholder, labels].join(" ")
              );
              return {
                tag: node.tagName.toLowerCase(), type: node.type || "",
                label: labels, text: (node.innerText || "").trim(),
                placeholder: node.placeholder || "",
                value: sensitive ? "[REDACTED]" : (node.value || "")
              };
            })"""
        )
        return {
            "url": page.url,
            "accessibility_tree": accessibility,
            "visible_text": page.locator("body").inner_text()[:8000],
            "fields": fields,
        }

    def validate_healing_locator(
        self,
        original: str | dict[str, Any],
        replacement: dict[str, Any],
        action: str,
    ) -> bool:
        """Require one visible, enabled element with compatible role/type."""
        if not isinstance(replacement, dict) or len(replacement) != 1:
            return False
        try:
            from testbench.core.steps import Step

            if action in {"click", "expect_visible"}:
                step = Step.model_validate({action: replacement})
                locator = self._find_locator(step.value, for_input=False)
            elif action in {"type", "select_option"}:
                Step.model_validate({action: {"field": replacement, "value": ""}})
                locator = self._find_locator(replacement, for_input=True)
            else:
                return False
            if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
                return False
            tag = locator.evaluate("node => node.tagName.toLowerCase()")
            role = locator.evaluate(
                """node => node.getAttribute("role") || (
                  node.tagName === "BUTTON" ? "button" :
                  node.tagName === "A" ? "link" :
                  node.tagName === "SELECT" ? "combobox" :
                  node.tagName === "TEXTAREA" ? "textbox" :
                  node.tagName === "INPUT" && ["checkbox","radio","submit","button"].includes(node.type)
                    ? node.type === "submit" || node.type === "button" ? "button" : node.type
                    : node.tagName === "INPUT" ? "textbox" : node.tagName.toLowerCase()
                )"""
            )
            _, original_role = _locator_description(original)
            if original_role and role.lower() != original_role.lower():
                return False
            if action == "type":
                return tag in {"input", "textarea"}
            if action == "select_option":
                return tag == "select"
            if action == "click":
                return role in {"button", "link", "checkbox", "radio", "menuitem", "option"}
            return True
        except (Error, LookupError, TypeError, ValueError):
            return False

    def _record_console(self, message: Any) -> None:
        if message.type != "error":
            return
        from testbench.ai.redaction import redact_text

        text, _ = redact_text(message.text)
        self.metadata["console_errors"].append(text[:1000])

    def _record_response(self, response: Any) -> None:
        if response.status < 400:
            return
        from testbench.ai.redaction import redact_text

        url, _ = redact_text(response.url)
        self.metadata["network_errors"].append({"url": url[:1000], "status": int(response.status)})

    def close(self) -> None:
        try:
            if self._context is not None:
                trace_path = self.artifact_dir / "trace.zip"
                self._context.tracing.stop(path=str(trace_path))
                self._context.close()
                if self._video is not None:
                    video_path = Path(self._video.path())
                    destination = self.artifact_dir / "videos" / video_path.name
                    if video_path.resolve() != destination.resolve():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        video_path.replace(destination)
        finally:
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Open a page before using the web driver.")
        return self._page

    def _find_locator(self, target: str | dict[str, Any], *, for_input: bool) -> Locator:
        page = self._require_page()
        frame = page.main_frame
        if isinstance(target, dict) and set(target) == {"frame_url", "locator"}:
            frame_url = str(target["frame_url"])
            frame = next(
                (candidate for candidate in page.frames if frame_url in candidate.url),
                None,
            )
            if frame is None:
                raise LookupError(f"Could not find recorded frame {frame_url!r}.")
            target = target["locator"]
        if isinstance(target, dict):
            if len(target) != 1:
                raise ValueError("A web locator mapping must contain exactly one strategy.")
            strategy, value = next(iter(target.items()))
            if strategy == "role":
                locator = frame.get_by_role(
                    value["role"],
                    name=value["name"],
                    exact=True,
                )
            elif strategy == "label":
                locator = frame.get_by_label(value, exact=True)
            elif strategy == "placeholder":
                locator = frame.get_by_placeholder(value, exact=True)
            elif strategy == "text":
                locator = frame.get_by_text(value, exact=True)
            elif strategy == "test_id":
                locator = frame.get_by_test_id(value)
            elif strategy == "id":
                escaped_id = str(value).replace("\\", "\\\\").replace('"', '\\"')
                locator = frame.locator(f'[id="{escaped_id}"]')
            elif strategy == "css":
                locator = frame.locator(value)
            elif strategy == "xpath":
                locator = frame.locator(f"xpath={value}")
            else:
                raise ValueError(f"Unsupported web locator strategy: {strategy}")
            try:
                if locator.count() == 1 and locator.first.is_visible():
                    return locator.first
            except Error:
                pass
            raise LookupError(f"Recorded locator {target!r} is not uniquely visible.")
        candidates: list[Locator]
        if for_input:
            candidates = [
                frame.get_by_label(target),
                frame.get_by_placeholder(target),
                frame.get_by_text(target),
                frame.get_by_role("textbox", name=target),
                frame.locator(target),
            ]
        else:
            candidates = [
                frame.get_by_text(target, exact=True),
                frame.get_by_label(target),
                frame.get_by_placeholder(target),
                frame.get_by_role("button", name=target),
                frame.get_by_role("link", name=target),
                frame.get_by_text(target),
                frame.locator(target),
            ]

        for candidate in candidates:
            try:
                first = candidate.first
                if first.count() and first.is_visible():
                    return first
            except Error:
                # Invalid CSS selectors and unsupported semantic queries may fall
                # through to the next locator strategy.
                continue
        raise LookupError(f"Could not find a visible element matching {target!r}.")


def _locator_description(target: str | dict[str, Any]) -> tuple[str, str | None]:
    if isinstance(target, str):
        return target, None
    if not isinstance(target, dict) or len(target) != 1:
        return "", None
    kind, value = next(iter(target.items()))
    if kind == "role" and isinstance(value, dict):
        return str(value.get("name", "")), str(value.get("role", ""))
    if isinstance(value, str) and kind in {"label", "text", "placeholder", "test_id", "id"}:
        return value, None
    return "", None

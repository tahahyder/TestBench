"""Playwright recorder for browser interactions, locators, and local screenshots."""

import re
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Dialog,
    Frame,
    Page,
    Playwright,
    sync_playwright,
)

from testbench.core.steps import Step
from testbench.recorder.base import RawEvent, Recorder
from testbench.recorder.locator import (
    choose_unique_locator,
    collect_dom_locator_info,
    score_locator_candidates,
)
from testbench.recorder.step_builder import StepBuilder

WEB_INIT_SCRIPT = r"""
(() => {
  const sensitiveWords = /password|token|secret|otp|cvv|card/i;
  const ignored = node => node?.closest?.("[data-testbench-toolbar]");
  const roleFor = element => {
    if (element.getAttribute("role")) return element.getAttribute("role");
    const tag = element.tagName.toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "input") {
      const type = (element.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "submit" || type === "button") return "button";
      if (type === "search") return "searchbox";
      return "textbox";
    }
    return null;
  };
  const labelFor = element => {
    if (element.labels?.length) return [...element.labels].map(label => label.innerText).join(" ").trim();
    const parent = element.closest("label");
    if (parent) return parent.innerText.trim();
    const id = element.id;
    return id ? document.querySelector(`label[for="${CSS.escape(id)}"]`)?.innerText?.trim() || "" : "";
  };
  const cssPath = element => {
    if (element.id && !/[0-9a-f]{8,}|\d{5,}/i.test(element.id)) return `#${CSS.escape(element.id)}`;
    const pieces = [];
    let node = element;
    while (node && node.nodeType === 1 && pieces.length < 4) {
      let part = node.tagName.toLowerCase();
      const testAttr = ["data-testid", "data-test", "data-qa"].find(name => node.hasAttribute(name));
      if (testAttr) {
        part += `[${testAttr}="${CSS.escape(node.getAttribute(testAttr))}"]`;
        pieces.unshift(part);
        break;
      }
      const classes = [...node.classList].filter(value => /^[a-zA-Z_-][\w-]{0,30}$/.test(value) && !/[0-9a-f]{8,}|\d{5,}/i.test(value)).slice(0, 1);
      if (classes.length) part += "." + CSS.escape(classes[0]);
      const siblings = node.parentElement ? [...node.parentElement.children].filter(sibling => sibling.tagName === node.tagName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      pieces.unshift(part);
      node = node.parentElement;
    }
    return pieces.join(" > ");
  };
  const describe = element => {
    const tag = element.tagName.toLowerCase();
    const type = (element.getAttribute("type") || "").toLowerCase();
    const label = labelFor(element);
    const sensitive = type === "password" || sensitiveWords.test([element.name, element.id, label, element.getAttribute("placeholder")].join(" "));
    const text = (sensitive ? "" : (element.innerText || element.value || element.getAttribute("aria-label") || "")).trim().replace(/\s+/g, " ").slice(0, 100);
    const name = element.getAttribute("aria-label") || label || element.getAttribute("title") || (["button", "a", "option"].includes(tag) ? text : "");
    return {
      tag,
      role: roleFor(element),
      name: name.slice(0, 100),
      label: label.slice(0, 100),
      field_name: (element.getAttribute("name") || "").slice(0, 100),
      placeholder: (element.getAttribute("placeholder") || "").slice(0, 100),
      text,
      id: element.id || "",
      "data-testid": element.getAttribute("data-testid") || "",
      "data-test": element.getAttribute("data-test") || "",
      "data-qa": element.getAttribute("data-qa") || "",
      css: cssPath(element),
      xpath: element.id ? `//*[@id=${JSON.stringify(element.id)}]` : `//${tag}`,
      sensitive,
      checked: !!element.checked,
      disabled: !!element.disabled,
      url: location.href,
      visible: !!(element.getClientRects().length && getComputedStyle(element).visibility !== "hidden")
    };
  };
  const emit = (type, element, value, extra = {}) => {
    if (!window.__testbench_event || ignored(element)) return;
    const info = element ? describe(element) : {};
    window.__testbench_pending_events = (window.__testbench_pending_events || 0) + 1;
    window.__testbench_event({
      type,
      target: info,
      value: info.sensitive ? null : value,
      metadata: {
        sensitive: !!info.sensitive,
        frame_url: location.href,
        ...extra
      }
    }).then(() => console.debug("__TESTBENCH_EVENT_DRAIN__"))
      .finally(() => window.__testbench_pending_events--);
  };
  let assertionMode = null;
  const addToolbar = () => {
    if (window.top !== window || document.querySelector("[data-testbench-toolbar]")) return;
    const host = document.createElement("div");
    host.setAttribute("data-testbench-toolbar", "");
    host.style.cssText = "all:initial;position:fixed;z-index:2147483647;top:8px;right:8px";
    const shadow = host.attachShadow({mode:"open"});
    shadow.innerHTML = `<style>
      nav{display:flex;gap:4px;padding:6px;border-radius:8px;background:#0f172a;color:#fff;box-shadow:0 2px 12px #0008;font:12px system-ui}
      button{border:0;border-radius:4px;padding:6px 8px;background:#0891b2;color:#fff;font:inherit;cursor:pointer}
      button[data-mode]{background:#334155}
    </style><nav>
      <button data-command="pause">Pause</button><button data-command="resume">Resume</button>
      <button data-mode="visible">Assert visible</button><button data-mode="text">Assert text</button>
      <button data-mode="url">Assert URL contains</button><button data-mode="enabled">Assert enabled/disabled</button>
      <button data-command="screenshot">Add screenshot</button><button data-command="wait">Add wait</button>
      <button data-command="stop">Stop</button>
    </nav>`;
    shadow.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.mode) {
        assertionMode = button.dataset.mode;
        if (assertionMode === "url") {
          const value = prompt("Record a URL substring to assert:");
          if (value) window.__testbench_event({type:"assert_url", value, target:{url:location.href}}).then(() => console.debug("__TESTBENCH_EVENT_DRAIN__"));
          assertionMode = null;
        }
        return;
      }
      window.__testbench_control?.(button.dataset.command);
    });
    document.documentElement.appendChild(host);
  };
  document.addEventListener("click", event => {
    if (ignored(event.target)) return;
    const element = event.target.closest("button,a,input,select,textarea,[role='button'],[role='link'],[tabindex]");
    if (!element) return;
    if (assertionMode) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const info = describe(element);
      const mode = assertionMode;
      assertionMode = null;
      emit(`assert_${mode}`, element, mode === "text" ? info.text : info);
      return;
    }
    const type = (element.getAttribute("type") || "").toLowerCase();
    if (type === "checkbox" || type === "radio" || element.tagName.toLowerCase() === "select") return;
    emit("click", element, null, {x:event.clientX, y:event.clientY});
  }, true);
  let inputTimer = null;
  document.addEventListener("input", event => {
    const element = event.target;
    if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) return;
    if (element instanceof HTMLInputElement && ["checkbox","radio","file","button","submit","reset"].includes(element.type)) return;
    clearTimeout(inputTimer);
    inputTimer = setTimeout(() => emit("input", element, element.value), 250);
  }, true);
  document.addEventListener("change", event => {
    const element = event.target;
    if (element instanceof HTMLSelectElement) emit("select", element, element.selectedOptions[0]?.text || element.value);
    else if (element instanceof HTMLInputElement && ["checkbox", "radio"].includes(element.type)) {
      emit(element.checked ? "check" : "uncheck", element, null, {checked:element.checked});
    } else if (element instanceof HTMLInputElement && element.type === "file") {
      emit("file_upload", element, null, {file_count:element.files?.length || 0});
    }
  }, true);
  document.addEventListener("keydown", event => {
    if (["Enter","Tab","Escape"].includes(event.key)) emit("keypress", event.target instanceof Element ? event.target : null, event.key.toLowerCase());
  }, true);
  let lastScroll = 0;
  document.addEventListener("scroll", event => {
    if (Date.now() - lastScroll < 250) return;
    lastScroll = Date.now();
    const target = event.target instanceof Element ? event.target : document.documentElement;
    const deltaY = event.deltaY || window.scrollY || 0;
    emit("scroll", target, {direction:deltaY < 0 ? "up" : "down", amount:Math.max(1, Math.abs(deltaY))});
  }, true);
  window.addEventListener("DOMContentLoaded", addToolbar, {once:true});
  addToolbar();
})();
"""


class WebRecorder(Recorder):
    """Capture browser events and convert them to the shared TestBench steps."""

    def __init__(self, *, on_event=None) -> None:
        super().__init__(on_event=on_event)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self.artifact_dir = Path("runs") / "recording"
        self._screenshots = 0
        self._previous_frame: Image.Image | None = None
        self.stop_requested = False
        self._stopping = False
        self._initial_navigation = True
        self._variables_by_field: dict[str, str] = {}
        self._pending_page_events: list[tuple[Frame, Page, dict[str, Any]]] = []

    def start(self, config: dict[str, Any]) -> None:
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError("Web recording requires config.url.")
        self.artifact_dir = Path(config.get("artifact_dir", "runs/recording"))
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        browser_name = str(config.get("browser", "chromium"))
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError(f"Unsupported recorder browser: {browser_name}.")
        browser_type = getattr(self._playwright, browser_name)
        self._browser = browser_type.launch(headless=bool(config.get("headless", False)))
        self._context = self._browser.new_context()
        self._context.expose_binding("__testbench_event", self._on_page_event)
        self._context.expose_binding("__testbench_control", self._on_toolbar_control)
        self._context.add_init_script(WEB_INIT_SCRIPT)
        self._context.on("page", self._attach_page)
        self.page = self._context.new_page()
        self._attach_page(self.page)
        self.page.goto(url, wait_until="domcontentloaded")
        self._initial_navigation = False
        self._take_screenshot(self.page, "initial")
        self._previous_frame = self._read_screenshot(
            str((self.artifact_dir / "screenshots" / "0001_initial.png").resolve())
        )
        self.emit(
            RawEvent(
                "navigation",
                time.monotonic(),
                target={"url": url},
                value=url,
            )
        )

    def stop(self) -> list[Step]:
        try:
            if self.page is not None and not self.page.is_closed():
                for frame in self.page.frames:
                    frame.wait_for_function(
                        "() => (window.__testbench_pending_events || 0) === 0",
                        timeout=5000,
                    )
                self._flush_pending()
                self._take_screenshot(self.page, "final")
            return StepBuilder().build(self.raw_events)
        finally:
            self._stopping = True
            self._close_browser()

    def pause(self) -> None:
        self.paused = True
        self._notify_control("paused")

    def resume(self) -> None:
        self.paused = False
        self._notify_control("resumed")

    def add_screenshot(self, label: str = "recorded") -> RawEvent:
        if self.page is None:
            raise RuntimeError("Start browser recording before taking screenshots.")
        screenshot = self._take_screenshot(self.page, label)
        event = RawEvent(
            "screenshot",
            time.monotonic(),
            value=label,
            screenshot=screenshot,
        )
        self.emit(event)
        return event

    def add_wait(self, seconds: float = 1.0) -> RawEvent:
        if seconds < 0:
            raise ValueError("Recorded wait duration cannot be negative.")
        event = RawEvent("wait", time.monotonic(), value=seconds)
        self.emit(event)
        return event

    def _on_page_event(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        if self._stopping:
            return
        frame = source.get("frame")
        page = source.get("page")
        if not isinstance(payload, dict) or not isinstance(frame, Frame):
            return
        if isinstance(page, Page):
            self._pending_page_events.append((frame, page, payload))

    def _on_console(self, page: Page, message: Any) -> None:
        if self._stopping or message.text != "__TESTBENCH_EVENT_DRAIN__":
            return
        pending = self._pending_page_events
        self._pending_page_events = []
        for frame, source_page, payload in pending:
            if source_page == page:
                self._process_page_event(frame, source_page, payload)
            else:
                self._pending_page_events.append((frame, source_page, payload))

    def _flush_pending(self) -> None:
        pending = self._pending_page_events
        self._pending_page_events = []
        for frame, page, payload in pending:
            self._process_page_event(frame, page, payload)

    def _process_page_event(
        self,
        frame: Frame,
        page: Page,
        payload: dict[str, Any],
    ) -> None:
        if self.paused:
            return
        event_type = str(payload.get("type", ""))
        target_info = payload.get("target") or {}
        if event_type in {"file_upload"}:
            self.warnings.append("File upload detected; add its local path manually.")
            self._notify_control("warning", detail=self.warnings[-1])
            return
        if event_type == "assert_url":
            event = RawEvent(
                "assert_url",
                time.monotonic(),
                target={"url": target_info.get("url", "")},
                value=str(payload.get("value", "")),
                screenshot=self._take_screenshot(page, "assert_url"),
            )
            self.emit(event)
            return
        if event_type == "visible_change":
            screenshot = self._take_screenshot(page, "visible_change")
            self.emit(
                RawEvent(
                    "visible_change",
                    time.monotonic(),
                    screenshot=screenshot,
                    metadata={"visible_change": True},
                )
            )
            return

        candidates = score_locator_candidates(collect_dom_locator_info(target_info))
        selected = choose_unique_locator(frame, collect_dom_locator_info(target_info))
        if selected is None:
            self.warnings.append("Could not verify a unique stable locator for a captured element.")
            self._notify_control("warning", detail=self.warnings[-1])
            locator_target: Any = target_info.get("text") or target_info.get("name")
        else:
            locator_target = selected.as_step_target()
            if frame.parent_frame is not None:
                locator_target = {
                    "frame_url": frame.url,
                    "locator": locator_target,
                }
        event_target = {
            "locator": locator_target,
            "text": target_info.get("text"),
            "role": target_info.get("role"),
            "name": target_info.get("name"),
            "frame_url": frame.url,
            "fallbacks": [item.as_step_target() for item in candidates if item != selected][:3],
        }
        metadata = dict(payload.get("metadata") or {})
        if "x" in metadata and "y" in metadata:
            event_target["x"] = int(metadata["x"])
            event_target["y"] = int(metadata["y"])
        event_value = payload.get("value")
        if metadata.get("sensitive"):
            variable_name = self._variable_for(target_info)
            metadata["placeholder"] = "${" + variable_name + "}"
            event_value = None
        screenshot = self._take_screenshot(page, event_type)
        event = RawEvent(
            type=event_type,
            timestamp=time.monotonic(),
            target=event_target,
            value=event_value,
            screenshot=screenshot,
            metadata=metadata,
        )
        self.emit(event)
        if event_type == "click" and self._has_visible_change(screenshot):
            self.emit(
                RawEvent(
                    "visible_change",
                    time.monotonic(),
                    screenshot=screenshot,
                    metadata={"visible_change": True},
                )
            )

    def _has_visible_change(self, screenshot: str | None) -> bool:
        frame = self._read_screenshot(screenshot)
        if frame is None:
            return False
        previous = self._previous_frame
        self._previous_frame = frame
        if previous is None or previous.size != frame.size:
            return previous is not None
        difference = ImageChops.difference(ImageOps.grayscale(previous), ImageOps.grayscale(frame))
        changed_pixels = sum(count for value, count in enumerate(difference.histogram()) if value > 0)
        return changed_pixels / (frame.width * frame.height) >= 0.001

    def _on_toolbar_control(self, source: dict[str, Any], command: str) -> None:
        if command == "pause":
            self.pause()
        elif command == "resume":
            self.resume()
        elif command == "screenshot":
            self.add_screenshot()
        elif command == "wait":
            self.add_wait()
        elif command == "stop":
            self.stop_requested = True
            self._notify_control("stop_requested")

    def _attach_page(self, page: Page) -> None:
        self.page = page
        page.on("console", lambda message, source_page=page: self._on_console(source_page, message))
        page.on("dialog", self._dismiss_dialog)
        page.on("download", lambda download: self._record_download(download.suggested_filename))
        page.on("filechooser", lambda _: self._record_file_chooser())
        page.on(
            "framenavigated",
            lambda frame: self._on_navigation(frame),
        )

    def _on_navigation(self, frame: Frame) -> None:
        if self._initial_navigation or frame.parent_frame is not None or self.paused:
            return
        event = RawEvent(
            "navigation",
            time.monotonic(),
            target={"url": frame.url},
            value=frame.url,
        )
        self.emit(event)

    def _dismiss_dialog(self, dialog: Dialog) -> None:
        try:
            dialog.dismiss()
        except Exception as exc:
            self.warnings.append(f"Could not dismiss browser dialog: {exc}")

    def _record_download(self, filename: str) -> None:
        self.warnings.append(
            f"Download {Path(filename).name!r} occurred; downloaded files are not automatically attached."
        )
        self._notify_control("warning", detail=self.warnings[-1])

    def _record_file_chooser(self) -> None:
        self.warnings.append("File upload detected; add its local path manually.")
        self._notify_control("warning", detail=self.warnings[-1])

    def _take_screenshot(self, page: Page | None, label: str) -> str | None:
        if page is None or page.is_closed():
            return None
        self._screenshots += 1
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:40] or "event"
        path = self.artifact_dir / "screenshots" / f"{self._screenshots:04d}_{safe_label}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        toolbar = page.locator("[data-testbench-toolbar]")
        try:
            page.evaluate(
                """() => {
                  const words = /password|token|secret|otp|cvv|card/i;
                  const saved = [];
                  for (const element of document.querySelectorAll("input,textarea")) {
                    const labels = element.labels ? [...element.labels].map(item => item.innerText).join(" ") : "";
                    const sensitive = element.type === "password" ||
                      words.test([element.name, element.id, element.placeholder, labels].join(" "));
                    if (!sensitive) continue;
                    saved.push([element, element.style.visibility, element.style.getPropertyPriority("visibility")]);
                    element.style.setProperty("visibility", "hidden", "important");
                  }
                  window.__testbench_hidden_sensitive_fields = saved;
                }"""
            )
            if toolbar.count():
                toolbar.evaluate("element => element.style.setProperty('display', 'none')")
            page.screenshot(path=str(path), full_page=False)
        finally:
            page.evaluate(
                """() => {
                  for (const [element, visibility, priority] of window.__testbench_hidden_sensitive_fields || []) {
                    if (visibility) element.style.setProperty("visibility", visibility, priority);
                    else element.style.removeProperty("visibility");
                  }
                  delete window.__testbench_hidden_sensitive_fields;
                }"""
            )
            if toolbar.count():
                toolbar.evaluate("element => element.style.removeProperty('display')")
        return str(path.resolve())

    @staticmethod
    def _read_screenshot(path: str | None) -> Image.Image | None:
        if not path:
            return None
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except OSError:
            return None

    def _variable_for(self, target_info: dict[str, Any]) -> str:
        field_key = "|".join(
            str(target_info.get(key, "")) for key in ("field_name", "id", "name", "label", "placeholder")
        )
        if field_key not in self._variables_by_field:
            parts = [
                re.sub(r"[^A-Za-z0-9]+", "_", str(target_info.get(key, ""))).strip("_").upper()
                for key in ("field_name", "id", "name", "label", "placeholder")
            ]
            base = next((part for part in parts if part), "")
            if not base:
                base = f"RECORDED_SECRET_{len(self.variables) + 1}"
            if base == "PASSWORD":
                base = "LOGIN_PASSWORD"
            if not base.endswith(("PASSWORD", "TOKEN", "SECRET", "OTP", "CVV", "CARD")):
                base += "_PASSWORD"
            original = base
            suffix = 2
            while base in self.variables:
                base = f"{original}_{suffix}"
                suffix += 1
            self.variables.append(base)
            self._variables_by_field[field_key] = base
        return self._variables_by_field[field_key]

    def _notify_control(self, kind: str, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event({"kind": kind, **payload})

    def _close_browser(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self.page = None


def _visible_difference(first: Image.Image, second: Image.Image) -> bool:
    if first.size != second.size:
        second = second.resize(first.size)
    difference = ImageChops.difference(ImageOps.grayscale(first), ImageOps.grayscale(second))
    histogram = difference.histogram()
    mean = sum(value * count for value, count in enumerate(histogram))
    return mean / (first.width * first.height * 255) > 0.01

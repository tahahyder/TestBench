const captureButton = document.querySelector("#inspect");
const screenCanvas = document.querySelector("#screen-canvas");
const screenImage = document.querySelector("#device-screen");
const overlays = document.querySelector("#element-overlays");
const errorMessage = document.querySelector("#inspector-error");
const yamlCode = document.querySelector("#selected-yaml");
const locatorCode = document.querySelector("#selected-locator");
const description = document.querySelector("#selected-description");
const copyButton = document.querySelector("#copy-yaml");
let inspectedElements = [];

function yamlString(value) {
  return JSON.stringify(value);
}

function locatorFor(element) {
  if (element.content_desc) return { accessibility_id: element.content_desc };
  if (element.text) return element.text;
  if (element.id) return { id: element.id };
  if (element.bounds) return { xpath: `//*[@bounds=${yamlString(element.bounds)}]` };
  return null;
}

function yamlFor(locator) {
  if (typeof locator === "string") return `- click: ${yamlString(locator)}`;
  const [key, value] = Object.entries(locator)[0];
  return `- click:\n    ${key}: ${yamlString(value)}`;
}

function boundsFor(value) {
  const match = /^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$/.exec(value || "");
  return match ? match.slice(1).map(Number) : null;
}

function chooseElement(index, button) {
  const element = inspectedElements[index];
  const locator = locatorFor(element);
  if (!locator) return;
  document.querySelector(".element-overlay.selected")?.classList.remove("selected");
  button.classList.add("selected");
  description.textContent = [
    element.text && `Text: ${element.text}`,
    element.content_desc && `Description: ${element.content_desc}`,
    element.id && `ID: ${element.id}`,
    `Class: ${element.class || "unknown"}`,
    `Clickable: ${element.clickable}`,
    `Bounds: ${element.bounds || "unknown"}`,
  ].filter(Boolean).join(" · ");
  locatorCode.textContent = typeof locator === "string"
    ? `click: ${yamlString(locator)}`
    : JSON.stringify(locator);
  yamlCode.textContent = yamlFor(locator);
  copyButton.disabled = false;
}

function drawElements() {
  overlays.replaceChildren();
  const renderedWidth = screenImage.clientWidth;
  const renderedHeight = screenImage.clientHeight;
  const scaleX = renderedWidth / screenImage.naturalWidth;
  const scaleY = renderedHeight / screenImage.naturalHeight;
  inspectedElements.forEach((element, index) => {
    const bounds = boundsFor(element.bounds);
    if (!bounds || !locatorFor(element)) return;
    const [left, top, right, bottom] = bounds;
    const button = document.createElement("button");
    button.className = "element-overlay";
    button.type = "button";
    button.title = [element.text, element.content_desc, element.id].filter(Boolean).join(" | ");
    button.style.left = `${left * scaleX}px`;
    button.style.top = `${top * scaleY}px`;
    button.style.width = `${Math.max(4, (right - left) * scaleX)}px`;
    button.style.height = `${Math.max(4, (bottom - top) * scaleY)}px`;
    button.addEventListener("click", () => chooseElement(index, button));
    overlays.append(button);
  });
}

async function inspectDevice() {
  captureButton.disabled = true;
  captureButton.textContent = "Capturing…";
  errorMessage.classList.add("hidden");
  try {
    const response = await fetch("/api/mobile/inspect");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    inspectedElements = payload.elements;
    const device = payload.metadata || {};
    document.querySelector("#device-info").textContent =
      `${device.device_model || "Android device"} · Android ${device.android_version || "version unknown"} · ${device.device_serial || ""}`;
    screenImage.onload = drawElements;
    screenCanvas.classList.remove("hidden");
    screenImage.src = payload.screenshot;
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.classList.remove("hidden");
  } finally {
    captureButton.disabled = false;
    captureButton.textContent = "Capture screen";
  }
}

captureButton.addEventListener("click", inspectDevice);
copyButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(yamlCode.textContent);
  copyButton.textContent = "Copied";
  window.setTimeout(() => { copyButton.textContent = "Copy YAML"; }, 1200);
});
window.addEventListener("resize", drawElements);

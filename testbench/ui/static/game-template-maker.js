const screen = document.querySelector("#screen");
const holder = document.querySelector("#screen-holder");
const selection = document.querySelector("#selection");
const statusMessage = document.querySelector("#status");
const templatesElement = document.querySelector("#templates");
const nameInput = document.querySelector("#template-name");
const yamlStep = document.querySelector("#yaml-step");
const copyButton = document.querySelector("#copy-yaml");
let selectionStart = null;
let crop = null;
let screenshotUrl = null;

function options() {
  const params = new URLSearchParams();
  for (const [key, selector] of [
    ["surface", "#surface"],
    ["window_title", "#window-title"],
    ["device", "#device"],
    ["url", "#url"],
    ["templates_dir", "#templates-dir"],
  ]) {
    const value = document.querySelector(selector).value.trim();
    if (value) params.set(key, value);
  }
  return params;
}

function templateParams() {
  const params = options();
  params.delete("window_title");
  params.delete("device");
  params.delete("url");
  params.delete("surface");
  return params;
}

async function captureScreen() {
  statusMessage.textContent = "Capturing…";
  const response = await fetch(`/api/game/screenshot?${options()}&t=${Date.now()}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Capture failed (${response.status})`);
  }
  if (screenshotUrl) URL.revokeObjectURL(screenshotUrl);
  screenshotUrl = URL.createObjectURL(await response.blob());
  screen.src = screenshotUrl;
  await new Promise((resolve, reject) => {
    screen.onload = resolve;
    screen.onerror = reject;
  });
  crop = null;
  selection.classList.add("hidden");
  document.querySelector("#save-template").disabled = true;
  statusMessage.textContent = `Screen captured (${screen.naturalWidth}×${screen.naturalHeight}). Drag to select a crop.`;
}

function imagePoint(event) {
  const bounds = screen.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(screen.naturalWidth, (event.clientX - bounds.left) * screen.naturalWidth / bounds.width)),
    y: Math.max(0, Math.min(screen.naturalHeight, (event.clientY - bounds.top) * screen.naturalHeight / bounds.height)),
  };
}

screen.addEventListener("pointerdown", event => {
  if (!screen.naturalWidth) return;
  screen.setPointerCapture(event.pointerId);
  selectionStart = imagePoint(event);
  crop = null;
});

screen.addEventListener("pointermove", event => {
  if (!selectionStart) return;
  const current = imagePoint(event);
  const left = Math.min(selectionStart.x, current.x);
  const top = Math.min(selectionStart.y, current.y);
  const right = Math.max(selectionStart.x, current.x);
  const bottom = Math.max(selectionStart.y, current.y);
  const bounds = screen.getBoundingClientRect();
  const scaleX = bounds.width / screen.naturalWidth;
  const scaleY = bounds.height / screen.naturalHeight;
  Object.assign(selection.style, {
    left: `${left * scaleX}px`,
    top: `${top * scaleY}px`,
    width: `${(right - left) * scaleX}px`,
    height: `${(bottom - top) * scaleY}px`,
  });
  selection.classList.remove("hidden");
});

screen.addEventListener("pointerup", event => {
  if (!selectionStart) return;
  const end = imagePoint(event);
  const left = Math.floor(Math.min(selectionStart.x, end.x));
  const top = Math.floor(Math.min(selectionStart.y, end.y));
  const right = Math.ceil(Math.max(selectionStart.x, end.x));
  const bottom = Math.ceil(Math.max(selectionStart.y, end.y));
  crop = { x: left, y: top, width: right - left, height: bottom - top };
  selectionStart = null;
  document.querySelector("#save-template").disabled = crop.width < 2 || crop.height < 2;
  statusMessage.textContent = `Selected ${crop.width}×${crop.height} pixels.`;
});

async function saveTemplate() {
  if (!crop) throw new Error("Capture a screen and select a crop first.");
  const payload = {
    ...crop,
    name: nameInput.value.trim(),
    surface: document.querySelector("#surface").value,
    window_title: document.querySelector("#window-title").value.trim() || null,
    device: document.querySelector("#device").value.trim() || "auto",
    url: document.querySelector("#url").value.trim() || null,
    templates_dir: document.querySelector("#templates-dir").value.trim() || null,
  };
  const response = await fetch("/api/game/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `Save failed (${response.status})`);
  yamlStep.textContent = body.yaml_step;
  yamlStep.classList.remove("hidden");
  copyButton.classList.remove("hidden");
  statusMessage.textContent = `Saved ${body.name}.`;
  await loadTemplates();
}

async function loadTemplates() {
  const response = await fetch(`/api/game/templates?${templateParams()}`);
  const templates = await response.json();
  if (!response.ok) throw new Error(templates.detail || `List failed (${response.status})`);
  templatesElement.replaceChildren();
  for (const template of templates) {
    const card = document.createElement("article");
    card.className = "template-card";
    const image = document.createElement("img");
    image.src = `${template.image_url}${template.image_url.includes("?") ? "&" : "?"}t=${Date.now()}`;
    image.alt = template.name;
    const title = document.createElement("strong");
    title.textContent = template.name;
    const matchButton = document.createElement("button");
    matchButton.className = "text-button";
    matchButton.textContent = "Test match now";
    const renameButton = document.createElement("button");
    renameButton.className = "text-button";
    renameButton.textContent = "Rename";
    renameButton.addEventListener("click", async () => {
      const newName = window.prompt("Template name", template.name.replace(/\.png$/i, ""));
      if (!newName) return;
      try {
        const params = templateParams();
        const suffix = params.toString() ? `?${params}` : "";
        const response = await fetch(
          `/api/game/templates/${encodeURIComponent(template.name)}/rename${suffix}`,
          {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: newName}),
          },
        );
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Rename failed.");
        yamlStep.textContent = body.yaml_step;
        yamlStep.classList.remove("hidden");
        copyButton.classList.remove("hidden");
        statusMessage.textContent = `Renamed to ${body.name}.`;
        await loadTemplates();
      } catch (error) {
        showError(error);
      }
    });
    const result = document.createElement("span");
    result.className = "template-result";
    matchButton.addEventListener("click", async () => {
      matchButton.disabled = true;
      try {
        const params = options();
        const response = await fetch(
          `/api/game/templates/${encodeURIComponent(template.name)}/match?${params}`,
          { method: "POST" },
        );
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Match check failed.");
        result.textContent = body.found
          ? `Confidence ${(body.confidence * 100).toFixed(1)}%`
          : "No match";
      } catch (error) {
        result.textContent = error.message;
      } finally {
        matchButton.disabled = false;
      }
    });
    card.append(image, title, renameButton, matchButton, result);
    templatesElement.append(card);
  }
}

function showError(error) {
  statusMessage.textContent = error.message;
}

document.querySelector("#capture").addEventListener("click", () => captureScreen().catch(showError));
document.querySelector("#save-template").addEventListener("click", () => saveTemplate().catch(showError));
document.querySelector("#refresh-templates").addEventListener("click", () => loadTemplates().catch(showError));
copyButton.addEventListener("click", () => navigator.clipboard.writeText(yamlStep.textContent));
document.querySelector("#surface").addEventListener("change", () => loadTemplates().catch(showError));
document.querySelector("#templates-dir").addEventListener("change", () => loadTemplates().catch(showError));
loadTemplates().catch(showError);

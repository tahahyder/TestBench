const target = document.querySelector("#target");
const statusElement = document.querySelector("#status");
const stepsElement = document.querySelector("#steps");
const eventsElement = document.querySelector("#events");
const selfCheckElement = document.querySelector("#self-check");
let steps = [];
let eventSource;

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function configureFields() {
  const type = target.value;
  document.querySelector("#url-field").classList.toggle("hidden", type === "android");
  document.querySelector("#device-field").classList.toggle("hidden", type === "web");
  document.querySelector("#package-field").classList.toggle("hidden", type !== "android");
  document.querySelector("#app-field").classList.toggle("hidden", type !== "android");
  document.querySelector("#surface-field").classList.toggle("hidden", type !== "game");
  document.querySelector("#window-field").classList.toggle(
    "hidden",
    type !== "game" || document.querySelector("#surface").value !== "desktop",
  );
}

function recorderConfig() {
  const type = target.value;
  if (type === "web") {
    return {url: document.querySelector("#url").value};
  }
  if (type === "android") {
    const config = {
      device: document.querySelector("#device").value || "auto",
      package: document.querySelector("#package").value,
      appium_url: "http://127.0.0.1:4723",
      reset: false,
    };
    const app = document.querySelector("#app").value;
    if (app) config.app = app;
    return config;
  }
  const surface = document.querySelector("#surface").value;
  return {
    surface,
    device: document.querySelector("#device").value || "auto",
    url: document.querySelector("#url").value,
    window_title: document.querySelector("#window-title").value || null,
  };
}

function renderSteps() {
  stepsElement.replaceChildren();
  steps.forEach((step, index) => {
    const row = document.createElement("div");
    row.className = "step-row";
    const editor = document.createElement("textarea");
    editor.className = "step-editor";
    editor.setAttribute("aria-label", `Step ${index + 1} YAML`);
    editor.value = JSON.stringify(step, null, 2);
    editor.addEventListener("change", () => {
      try {
        const parsed = JSON.parse(editor.value);
        if (
          !parsed ||
          typeof parsed !== "object" ||
          Array.isArray(parsed) ||
          Object.keys(parsed).length !== 1
        ) {
          throw new Error("A step must be one action mapping.");
        }
        steps[index] = parsed;
        editor.setCustomValidity("");
      } catch (error) {
        editor.setCustomValidity(error.message);
        editor.reportValidity();
      }
    });
    const controls = document.createElement("div");
    const insert = document.createElement("button");
    insert.textContent = "Insert before";
    insert.addEventListener("click", () => insertStep(index));
    controls.append(insert);
    for (const [label, delta] of [["↑", -1], ["↓", 1]]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.disabled = index + delta < 0 || index + delta >= steps.length;
      button.addEventListener("click", () => {
        [steps[index], steps[index + delta]] = [steps[index + delta], steps[index]];
        renderSteps();
      });
      controls.append(button);
    }
    const remove = document.createElement("button");
    remove.textContent = "Delete";
    remove.addEventListener("click", () => {
      steps.splice(index, 1);
      renderSteps();
    });
    controls.append(remove);
    row.append(editor, controls);
    stepsElement.append(row);
  });
}

function insertStep(index) {
  const source = window.prompt(
    "Enter one TestBench step as a JSON action mapping.",
    '{"wait": 1}',
  );
  if (source === null) return;
  try {
    const parsed = JSON.parse(source);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("A step must be one action mapping.");
    }
    steps.splice(index, 0, parsed);
    renderSteps();
  } catch (error) {
    window.alert(error.message);
  }
}

function renderEvent(event) {
  const card = document.createElement("div");
  card.className = "step-row";
  const label = document.createElement("span");
  label.textContent = `${event.type}: ${event.target?.name || event.target?.text || ""}`;
  card.append(label);
  if (event.screenshot_url) {
    const image = document.createElement("img");
    image.src = event.screenshot_url;
    image.alt = `Screenshot after ${event.type}`;
    image.className = "event-thumb";
    card.append(image);
  }
  eventsElement.prepend(card);
}

function subscribeEvents() {
  eventSource?.close();
  eventSource = new EventSource("/api/recorder/events");
  eventSource.onmessage = ({data}) => {
    const payload = JSON.parse(data);
    if (payload.kind === "raw_event") renderEvent(payload.event);
    if (payload.kind === "steps") {
      steps = payload.steps;
      renderSteps();
    }
    if (payload.status) statusElement.textContent = payload.status;
    if (payload.kind === "error") statusElement.textContent = payload.detail;
    if (payload.kind === "stopped") {
      steps = payload.steps;
      renderSteps();
      statusElement.textContent = "Stopped";
      toggleControls(false);
    }
  };
  eventSource.onerror = () => eventSource?.close();
}

function toggleControls(active) {
  document.querySelector("#start").disabled = active;
  document.querySelector("#pause").disabled = !active;
  document.querySelector("#resume").disabled = !active;
  document.querySelector("#stop").disabled = !active;
  document.querySelector("#replay").disabled = active || steps.length === 0;
  document.querySelector("#save").disabled = active || steps.length === 0;
}

async function saveEdits() {
  await requestJson("/api/recorder/steps", {
    method: "PATCH",
    body: JSON.stringify({steps}),
  });
}

target.addEventListener("change", configureFields);
document.querySelector("#surface").addEventListener("change", configureFields);
document.querySelector("#add-step").addEventListener("click", () => insertStep(steps.length));
document.querySelector("#start").addEventListener("click", async () => {
  try {
    const config = recorderConfig();
    if (target.value === "android" && !config.package && !config.app) {
      throw new Error("Enter an Android package or APK path.");
    }
    const response = await requestJson("/api/recorder/start", {
      method: "POST",
      body: JSON.stringify({target: target.value, config, name: document.querySelector("#name").value}),
    });
    steps = [];
    eventsElement.replaceChildren();
    renderSteps();
    statusElement.textContent = response.status;
    toggleControls(true);
    subscribeEvents();
  } catch (error) {
    window.alert(error.message);
  }
});
document.querySelector("#pause").addEventListener("click", async () => {
  try { await requestJson("/api/recorder/pause", {method: "POST"}); statusElement.textContent = "Paused"; }
  catch (error) { window.alert(error.message); }
});
document.querySelector("#resume").addEventListener("click", async () => {
  try { await requestJson("/api/recorder/resume", {method: "POST"}); statusElement.textContent = "Recording"; }
  catch (error) { window.alert(error.message); }
});
document.querySelector("#stop").addEventListener("click", async () => {
  try {
    const response = await requestJson("/api/recorder/stop", {method: "POST"});
    steps = response.steps;
    renderSteps();
    toggleControls(false);
    statusElement.textContent = "Stopped; replay self-check is running.";
    eventSource?.close();
    await saveEdits();
    window.setTimeout(loadStatus, 700);
  } catch (error) { window.alert(error.message); }
});
document.querySelector("#replay").addEventListener("click", async () => {
  try {
    await saveEdits();
    const result = await requestJson("/api/recorder/replay", {method: "POST"});
    selfCheckElement.textContent = `${result.status.toUpperCase()} — ${result.report_path || result.run_id}`;
  } catch (error) { window.alert(error.message); }
});
document.querySelector("#save").addEventListener("click", async () => {
  try {
    await saveEdits();
    const result = await requestJson("/api/recorder/save", {
      method: "POST",
      body: JSON.stringify({name: document.querySelector("#name").value}),
    });
    statusElement.textContent = `Saved: ${result.path}`;
  } catch (error) { window.alert(error.message); }
});

async function loadStatus() {
  try {
    const result = await requestJson("/api/recorder");
    if (result.self_check) {
      selfCheckElement.textContent = `Self-check: ${result.self_check.status}`;
      if (result.self_check.report_path) {
        const link = document.createElement("a");
        link.href = `/api/runs/${result.self_check.run_id}/report`;
        link.textContent = "Open replay report";
        selfCheckElement.append(" ", link);
      }
      for (const warning of result.self_check.flakiness_warnings || []) {
        const item = document.createElement("p");
        item.textContent = `⚠ ${warning}`;
        selfCheckElement.append(item);
      }
    }
  } catch (error) {
    selfCheckElement.textContent = error.message;
  }
}

configureFields();
toggleControls(false);

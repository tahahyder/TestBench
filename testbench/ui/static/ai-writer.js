const statusElement = document.querySelector("#status");
const yamlElement = document.querySelector("#yaml");
let aiConfig;
let generatedByAI = false;
let aiRunId = null;

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function showDiff(before, after) {
  const oldLines = before.split("\n");
  const newLines = after.split("\n");
  const commonStart = oldLines.findIndex((line, index) => line !== newLines[index]);
  const start = commonStart < 0 ? Math.min(oldLines.length, newLines.length) : commonStart;
  let suffix = 0;
  while (
    suffix < oldLines.length - start &&
    suffix < newLines.length - start &&
    oldLines[oldLines.length - 1 - suffix] === newLines[newLines.length - 1 - suffix]
  ) suffix += 1;
  const removed = oldLines.slice(start, oldLines.length - suffix);
  const added = newLines.slice(start, newLines.length - suffix);
  document.querySelector("#diff").textContent = [
    `--- previous (${removed.length} lines)`,
    ...removed.map((line) => `- ${line}`),
    `+++ generated (${added.length} lines)`,
    ...added.map((line) => `+ ${line}`),
    "Review this diff before saving the draft.",
  ].join("\n");
}

function updateButtons() {
  const ready = Boolean(aiConfig?.enabled && aiConfig?.consented);
  document.querySelector("#generate").disabled = !ready;
  document.querySelector("#suggest").disabled = !ready;
  document.querySelector("#validate").disabled = !yamlElement.value.trim();
  document.querySelector("#dry-run").disabled = !yamlElement.value.trim();
  document.querySelector("#save").disabled = !yamlElement.value.trim();
}

async function loadConfig() {
  aiConfig = await requestJson("/api/ai/config");
  document.querySelector("#provider-status").textContent =
    `AI ${aiConfig.enabled ? "enabled" : "disabled"} · provider ${aiConfig.provider} · model ${aiConfig.model}`;
  document.querySelector("#consent-notice").textContent =
    `Page content and screenshots will be sent to ${aiConfig.provider}. Use ollama for fully local processing.`;
  document.querySelector("#consent-check").checked = aiConfig.consented;
  statusElement.textContent = aiConfig.enabled
    ? "Consent is provider-specific and stored locally."
    : "Enable [ai].enabled in testbench.toml, then restart the app.";
  updateButtons();
}

async function loadTests() {
  const tests = await requestJson("/api/tests");
  const select = document.querySelector("#suggest-test");
  select.replaceChildren();
  for (const test of tests) {
    const option = document.createElement("option");
    option.value = test.path;
    option.textContent = test.name;
    select.append(option);
  }
}

document.querySelector("#consent").addEventListener("click", async () => {
  try {
    const accepted = document.querySelector("#consent-check").checked;
    await requestJson("/api/ai/consent", {
      method: "POST",
      body: JSON.stringify({provider: aiConfig.provider, accepted}),
    });
    await loadConfig();
  } catch (error) {
    window.alert(error.message);
  }
});

document.querySelector("#generate").addEventListener("click", async () => {
  statusElement.textContent = "Generating and validating…";
  try {
    const result = await requestJson("/api/ai/generate", {
      method: "POST",
      body: JSON.stringify({
        instruction: document.querySelector("#instruction").value,
        target: document.querySelector("#target").value,
        url: document.querySelector("#url").value || null,
        iterate: document.querySelector("#iterate").checked,
      }),
    });
    const previous = yamlElement.value;
    yamlElement.value = result.yaml;
    generatedByAI = true;
    aiRunId = result.ai_run_id;
    showDiff(previous, result.yaml);
    const attempts = result.attempts || [];
    statusElement.textContent = `Generated with ${result.provider} / ${result.model}. ${attempts.length ? `Iteration rounds: ${attempts.length - 1}. ` : ""}Review the draft diff before saving.`;
    updateButtons();
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.querySelector("#validate").addEventListener("click", async () => {
  try {
    const result = await requestJson("/api/ai/validate", {
      method: "POST",
      body: JSON.stringify({yaml: yamlElement.value}),
    });
    statusElement.textContent = `Valid: ${result.steps} steps for ${result.target}.`;
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.querySelector("#dry-run").addEventListener("click", async () => {
  statusElement.textContent = "Running draft…";
  try {
    const result = await requestJson("/api/ai/dry-run", {
      method: "POST",
      body: JSON.stringify({
        yaml: yamlElement.value,
        ai_generated: generatedByAI,
        ai_run_id: aiRunId,
      }),
    });
    statusElement.textContent = `${result.status.toUpperCase()} · ${result.report_path || result.run_id}`;
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.querySelector("#save").addEventListener("click", async () => {
  try {
    const name = document.querySelector("#name").value;
    const result = await requestJson(`/api/ai/save?name=${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({yaml: yamlElement.value, ai_generated: generatedByAI}),
    });
    statusElement.textContent = `Saved locally: ${result.path}`;
    await loadTests();
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.querySelector("#suggest").addEventListener("click", async () => {
  const drafts = document.querySelector("#drafts");
  drafts.replaceChildren();
  try {
    const result = await requestJson("/api/ai/suggest", {
      method: "POST",
      body: JSON.stringify({test_name: document.querySelector("#suggest-test").value}),
    });
    result.drafts.forEach((draftItem) => {
      const draft = draftItem.test;
      const card = document.createElement("article");
      card.className = "draft";
      const heading = document.createElement("h3");
      heading.textContent = draft.name;
      const preview = document.createElement("pre");
      preview.textContent = draftItem.yaml;
      const accept = document.createElement("button");
      accept.className = "button";
      accept.textContent = "Accept draft";
      accept.addEventListener("click", () => {
        yamlElement.value = draftItem.yaml;
        generatedByAI = true;
        statusElement.textContent = "Draft loaded; review the YAML diff before saving.";
        updateButtons();
        showDiff("", draftItem.yaml);
        window.scrollTo({top: 0, behavior: "smooth"});
      });
      const reject = document.createElement("button");
      reject.textContent = "Reject draft";
      reject.addEventListener("click", () => card.remove());
      card.append(heading, preview, accept, reject);
      drafts.append(card);
    });
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.querySelector("#make-data").addEventListener("click", async () => {
  try {
    const fields = JSON.parse(document.querySelector("#data-fields").value);
    const result = await requestJson("/api/ai/data", {
      method: "POST",
      body: JSON.stringify({
        fields,
        seed: Number(document.querySelector("#seed").value),
        context: document.querySelector("#data-context").value || null,
      }),
    });
    document.querySelector("#fake-data").textContent = JSON.stringify(result.data, null, 2);
  } catch (error) {
    document.querySelector("#fake-data").textContent = error.message;
  }
});

async function loadHealingHistory() {
  try {
    const rows = await requestJson("/api/ai/healing");
    const container = document.querySelector("#healing-history");
    container.replaceChildren();
    for (const record of rows) {
      const row = document.createElement("article");
      row.className = "draft";
      const label = document.createElement("p");
      label.textContent = `Run ${record.run_id} · step ${record.step_index} · ${record.method} · ${record.confidence.toFixed(2)}`;
      const code = document.createElement("pre");
      code.textContent = `${JSON.stringify(record.old_locator)} → ${JSON.stringify(record.new_locator)}`;
      row.append(label, code);
      if (record.accepted !== true) {
        const accept = document.createElement("button");
        accept.textContent = "Accept and apply fix";
        accept.addEventListener("click", async () => {
          await requestJson(`/api/ai/healing/${record.id}/decision`, {
            method: "POST", body: JSON.stringify({accepted: true}),
          });
          await requestJson(`/api/ai/healing/${record.id}/apply`, {method: "POST"});
          await loadHealingHistory();
        });
        const reject = document.createElement("button");
        reject.textContent = "Reject";
        reject.addEventListener("click", async () => {
          await requestJson(`/api/ai/healing/${record.id}/decision`, {
            method: "POST", body: JSON.stringify({accepted: false}),
          });
          await loadHealingHistory();
        });
        row.append(accept, reject);
      } else {
        const applied = document.createElement("span");
        applied.textContent = "Accepted";
        row.append(applied);
      }
      container.append(row);
    }
  } catch (error) {
    document.querySelector("#healing-history").textContent = error.message;
  }
}

yamlElement.addEventListener("input", updateButtons);
loadConfig().catch((error) => { statusElement.textContent = error.message; });
loadTests().catch((error) => { document.querySelector("#suggest-test").textContent = error.message; });
loadHealingHistory();

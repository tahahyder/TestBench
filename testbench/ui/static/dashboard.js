const testsElement = document.querySelector("#tests");
const historyElement = document.querySelector("#history");
const emptyTests = document.querySelector("#empty-tests");
const activeRuns = new Set();

async function requestJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

async function loadTests() {
  const tests = await requestJson("/api/tests");
  testsElement.replaceChildren();
  emptyTests.classList.toggle("hidden", tests.length > 0);
  for (const test of tests) {
    const row = document.createElement("div");
    row.className = "test-row";
    const name = document.createElement("div");
    const title = document.createElement("span");
    title.className = "test-title";
    title.textContent = test.name;
    const file = document.createElement("span");
    file.className = "test-file";
    file.textContent = test.path;
    name.append(title, file);
    const button = document.createElement("button");
    button.className = "button run-button";
    button.textContent = "Run";
    button.addEventListener("click", () => startTest(test.name, button));
    row.append(name, button);
    testsElement.append(row);
  }
}

async function startTest(name, button) {
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    const run = await requestJson(`/api/tests/${encodeURIComponent(name)}/run`, { method: "POST" });
    activeRuns.add(run.run_id);
    await pollRun(run.run_id, button);
  } catch (error) {
    window.alert(error.message);
    button.disabled = false;
    button.textContent = "Run";
  }
}

async function pollRun(runId, button) {
  const response = await requestJson(`/api/runs/${runId}`);
  button.textContent = `${response.status} (${response.steps?.length || 0} steps)`;
  if (response.status === "queued" || response.status === "running") {
    window.setTimeout(() => pollRun(runId, button).catch(showError), 800);
    return;
  }
  activeRuns.delete(runId);
  button.disabled = false;
  button.textContent = "Run";
  await loadHistory();
}

async function loadHistory() {
  const runs = await requestJson("/api/runs?limit=30");
  historyElement.replaceChildren();
  for (const run of runs) {
    const row = document.createElement("tr");
    row.className = "history-row";
    const values = [run.test_name, run.status, new Date(run.started_at).toLocaleString(), `${run.duration_seconds.toFixed(2)}s`];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.className = "history-cell";
      cell.textContent = value;
      if (value === run.status) cell.classList.add(`status-${run.status}`);
      row.append(cell);
    }
    const reportCell = document.createElement("td");
    reportCell.className = "history-cell";
    if (run.report_path || run.status === "passed" || run.status === "failed") {
      const link = document.createElement("a");
      link.href = `/api/runs/${encodeURIComponent(run.run_id)}/report`;
      link.textContent = "Open report";
      link.className = "report-link";
      reportCell.append(link);
    }
    if (run.status === "failed") {
      const explain = document.createElement("button");
      explain.textContent = "Explain failure";
      explain.className = "text-button";
      explain.addEventListener("click", async () => {
        explain.disabled = true;
        try {
          const result = await requestJson(`/api/runs/${encodeURIComponent(run.run_id)}/explain`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({}),
          });
          const section = document.createElement("div");
          section.textContent = result.ai?.explanation || result.rule_based.explanation;
          reportCell.append(section);
          if (result.ai) {
            const refresh = document.createElement("a");
            refresh.href = `/api/runs/${encodeURIComponent(run.run_id)}/report`;
            refresh.textContent = "Updated report";
            reportCell.append(refresh);
          }
        } catch (error) {
          window.alert(error.message);
        } finally {
          explain.disabled = false;
        }
      });
      reportCell.append(explain);
    }
    row.append(reportCell);
    historyElement.append(row);
  }
}

async function loadSetupStatus() {
  const status = document.querySelector("#setup-status");
  if (!status) return;
  const health = await requestJson("/api/health");
  status.textContent = health.web_ready
    ? `Web testing is ready (${health.python_version}).`
    : `Web setup needs attention: ${health.web_message}`;
}

function showError(error) {
  window.console.error(error);
}

document.querySelector("#refresh").addEventListener("click", () => loadHistory().catch(showError));
Promise.all([loadTests(), loadHistory(), loadSetupStatus()]).catch(showError);

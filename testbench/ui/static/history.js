async function loadTrends() {
  const status = document.querySelector("#trend-status");
  try {
    const response = await fetch("/api/history/trends");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    status.textContent = `Summarized ${data.runs} recent runs.`;
    const trend = document.querySelector("#pass-rate");
    trend.replaceChildren();
    if (!data.pass_rate.length) {
      trend.textContent = "No runs recorded yet.";
    }
    for (const point of data.pass_rate) {
      const row = document.createElement("p");
      row.textContent = `${point.date}: ${(point.pass_rate * 100).toFixed(0)}% (${point.passed}/${point.total})`;
      trend.append(row);
    }
    renderList("#slowest", data.slowest, item => `${item.test_name}: ${item.duration.toFixed(2)}s max`);
    renderList("#flakiest", data.flakiest, item => `${item.test_name}: ${item.flaky_runs} flaky run(s)`);
  } catch (error) {
    status.textContent = error.message;
  }
}

function renderList(selector, items, label) {
  const list = document.querySelector(selector);
  list.replaceChildren();
  for (const item of items) {
    const row = document.createElement("li");
    row.textContent = label(item);
    list.append(row);
  }
  if (!items.length) {
    const row = document.createElement("li");
    row.textContent = "No data yet.";
    list.append(row);
  }
}

loadTrends();

/* Painel de migração Redmine -> GLPI.
 *
 * No framework and no CDN: the page is served from localhost next to a tool
 * that holds API tokens, so nothing here loads from the network.
 *
 * Report and log text is written with textContent only. It carries Redmine data
 * verbatim, and the report is the primary functional requirement - it must
 * render exactly as the .txt file, never as markup.
 */
"use strict";

const UI = JSON.parse(document.getElementById("ui-strings").textContent);
const ACCEPT = new Set(UI.UI_CONFIRM_ACCEPT_WORDS || ["sim"]);

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ helpers */

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function postJSON(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Classify one line of PT-BR output. The wording carries the meaning; the
 *  colour only makes it findable in 300 lines. */
function lineClass(line) {
  if (/^=+$|^-+$/.test(line.trim()) && line.trim().length > 20) return "l-rule";
  if (line.includes("[FALHA]") || line.includes("[ERRO]") || line.includes("[PERDIDO]")) return "l-fail";
  if (line.includes("[AVISO]")) return "l-warn";
  if (line.includes("[OK]")) return "l-ok";
  if (/^\d+\. [A-ZÁÂÃÉÊÍÓÔÕÚÇ]/.test(line) || /^[A-ZÁÂÃÉÊÍÓÔÕÚÇ][A-ZÁÂÃÉÊÍÓÔÕÚÇ \-—(),0-9]{6,}$/.test(line)) {
    return "l-head";
  }
  return "";
}

/* A batch prints hundreds of thousands of lines. The server already caps what
 * it retains (MAX_BATCH_LOG_EVENTS); this caps what the DOM holds, which is a
 * separate budget - a <pre> with 300k <span> children stops scrolling long
 * before the process runs out of memory. The complete record is on disk under
 * reports/<execução>/, which is what UI_BATCH_LOG_TRUNCATED tells the reader. */
const MAX_CONSOLE_LINES = 4000;

function appendLines(target, text) {
  const atBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 40;
  const fragment = document.createDocumentFragment();
  for (const line of String(text).split("\n")) {
    const span = document.createElement("span");
    const css = lineClass(line);
    if (css) span.className = css;
    span.textContent = line + "\n";
    fragment.appendChild(span);
  }
  target.appendChild(fragment);
  while (target.childElementCount > MAX_CONSOLE_LINES) {
    target.removeChild(target.firstChild);
  }
  if (atBottom) target.scrollTop = target.scrollHeight;
}

function renderText(target, text) {
  target.textContent = "";
  appendLines(target, text);
  target.scrollTop = 0;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function kpi(label, value, note, variant) {
  const tile = el("div", "kpi" + (variant ? " is-" + variant : ""));
  tile.appendChild(el("span", "kpi-label", label));
  tile.appendChild(el("span", "kpi-value", value));
  if (note) tile.appendChild(el("span", "kpi-note", note));
  return tile;
}

/** Status is never colour alone: every badge carries an icon and a sentence. */
function badge(text, variant) {
  const node = el("span", "badge is-" + variant);
  node.appendChild(el("span", "icon", variant === "good" ? "✓" : "!"));
  node.appendChild(el("span", null, text));
  return node;
}

function fill(template, values) {
  return String(template).replace(/\{(\w+)\}/g, (_, key) =>
    values[key] === undefined ? "" : values[key]
  );
}

function showError(node, message) {
  node.textContent = message;
  node.hidden = false;
}

/* --------------------------------------------------------------- navigation */

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("is-active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("is-active"));
    button.classList.add("is-active");
    $("view-" + button.dataset.view).classList.add("is-active");
    // Migração and Lote each own a mode switch. The banner warns about the
    // view you are looking at, so it follows the view rather than whichever
    // switch was touched last.
    const active = button.dataset.view === "batch" ? batchMode : mode;
    $("apply-banner").hidden =
      active !== "apply" || !["migration", "batch"].includes(button.dataset.view);

    if (button.dataset.view === "history") loadHistory();
    if (button.dataset.view === "config") loadConfig();
    if (button.dataset.view === "batch") loadBatchRuns();
  });
});

/* ------------------------------------------------------------------- health */

async function checkHealth() {
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.className = "chip chip-idle";
    chip.querySelector(".chip-state").textContent = UI.UI_STATUS_CHECKING;
  });
  let data;
  try {
    data = await api("/api/health");
  } catch (error) {
    data = { glpi: { ok: false, detail: error.message }, redmine: { ok: false, detail: error.message } };
  }
  document.querySelectorAll(".chip").forEach((chip) => {
    const state = data[chip.dataset.system] || { ok: false };
    chip.className = "chip " + (state.ok ? "is-ok" : "is-bad");
    chip.querySelector(".chip-state").textContent = state.ok
      ? UI.UI_STATUS_ONLINE
      : UI.UI_STATUS_OFFLINE;
    chip.title = state.detail || "";
  });
}

document.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", checkHealth));

/* ---------------------------------------------------------------- migration */

let mode = "dry";
let currentJob = null;
let stream = null;

$("mode").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-mode]");
  if (!button) return;
  mode = button.dataset.mode;
  $("mode").querySelectorAll("button").forEach((b) => {
    const active = b === button;
    b.classList.toggle("is-active", active);
    b.setAttribute("aria-checked", String(active));
  });
  $("apply-banner").hidden = mode !== "apply";
  $("run").textContent = mode === "apply" ? UI.UI_RUN_APPLY : UI.UI_RUN_DRY_RUN;
});

$("run").addEventListener("click", async () => {
  const issue = parseInt($("issue").value, 10);
  $("run-error").hidden = true;
  if (!issue || issue <= 0) {
    showError($("run-error"), UI.UI_ISSUE_INVALID);
    return;
  }

  $("console").textContent = "";
  $("console-card").hidden = false;
  $("summary").hidden = true;
  $("report-card").hidden = true;
  setRunning(true);

  try {
    const { job_id } = await postJSON("/api/migrate", { issue, mode });
    currentJob = job_id;
    $("download-report").href = `/api/jobs/${job_id}/report`;
    listen(job_id, {
      console: $("console"),
      onReport: (data) => {
        renderText($("report"), data.text);
        renderSummary(data.summary);
        $("report-card").hidden = false;
        $("summary").hidden = false;
      },
      // Called with the event payload; the single-issue gate wants the default
      // wording, so the argument is dropped rather than passed through.
      onAwaitingConfirm: () => openConfirm(),
      onError: (detail) => showError($("run-error"), detail),
      onDone: () => setRunning(false),
    });
  } catch (error) {
    setRunning(false);
    showError($("run-error"), error.message);
  }
});

function setRunning(running) {
  $("run").disabled = running;
  $("run").textContent = running
    ? UI.UI_RUNNING
    : mode === "apply"
    ? UI.UI_RUN_APPLY
    : UI.UI_RUN_DRY_RUN;
  $("run-spinner").hidden = !running;
}

function renderSummary(summary) {
  const row = $("kpi-row");
  const chips = $("warnings");
  row.textContent = "";
  chips.textContent = "";
  if (!summary) return;

  row.appendChild(
    kpi(
      UI.UI_CARD_PROJECT,
      summary.glpi_project_id || "—",
      summary.glpi_project_id ? `RDM ${summary.issue_id}` : `RDM ${summary.issue_id} — a criar`,
      "accent"
    )
  );
  row.appendChild(
    kpi(UI.UI_CARD_TASKS, summary.tasks, summary.tasks_written ? `${summary.tasks_written} criada(s)` : null)
  );
  row.appendChild(
    kpi(
      UI.UI_CARD_FATURAMENTO,
      summary.faturamento,
      summary.faturamento_written ? `${summary.faturamento_written} gravada(s)` : null
    )
  );
  row.appendChild(kpi(UI.UI_CARD_WRITTEN, summary.fields.written, "de " + summary.fields.total + " campos"));
  row.appendChild(
    kpi(
      UI.UI_CARD_IGNORED,
      summary.ignored,
      summary.fields.empty_source + " sem valor na origem",
      summary.ignored ? "warning" : null
    )
  );

  // summary.missing_mandatory is deliberately NOT badged here (removed
  // 2026-08-19). Its badge read "bloqueia a gravação", which GLPI stopped doing
  // when every field of container 15 went to mandatory = 0 - measured on the
  // 2026-08-07 sweep and again on 2026-08-19, and proven by writing RDM 19074
  // to project 1292 with all three of tracker 39's empty columns. The badge
  // never fired before CEMIG entered scope (tracker 14 fills all five), so the
  // claim went unnoticed; on the six CEMIG projects it was a red "do not run
  // this" on every single one.
  //
  // The count itself is still in the summary payload and the three columns are
  // still named in section 5 of the full report, worded conditionally. If GLPI
  // ever re-flags them, that section is where it shows - do not restore this
  // badge without making it read the live flag instead of the hard-coded
  // MANDATORY_CONTAINER15_COLUMNS guard list.
  const warnings = [
    [summary.unresolved, UI.UI_WARN_UNRESOLVED, "warning"],
    [summary.missing_mandatory_faturamento, UI.UI_WARN_MANDATORY_FATURAMENTO, "warning"],
    [summary.skipped_children, UI.UI_WARN_SKIPPED, "warning"],
    [summary.tree_failures, UI.UI_WARN_FAILURES, "critical"],
    [summary.tree_cycles, UI.UI_WARN_CYCLES, "critical"],
    [summary.faturamento_degraded, UI.UI_WARN_DEGRADED, "critical"],
  ];
  for (const [count, template, variant] of warnings) {
    if (count) chips.appendChild(badge(fill(template, { count }), variant));
  }
  if (!summary.integrity_ok) chips.appendChild(badge(UI.UI_WARN_INTEGRITY, "critical"));
  if (!chips.childElementCount) {
    chips.appendChild(badge("Nenhum aviso — nada foi perdido em silêncio.", "good"));
  }
}

/* ------------------------------------------------------------------ streams */

function listen(jobId, handlers) {
  if (stream) stream.close();
  stream = new EventSource(`/api/jobs/${jobId}/stream`);

  stream.addEventListener("log", (event) => {
    appendLines(handlers.console, JSON.parse(event.data).data);
  });
  stream.addEventListener("report", (event) => {
    if (handlers.onReport) handlers.onReport(JSON.parse(event.data).data);
  });
  stream.addEventListener("audit_result", (event) => {
    if (handlers.onAuditResult) handlers.onAuditResult(JSON.parse(event.data).data);
  });
  stream.addEventListener("awaiting_confirm", (event) => {
    // The payload carries the queue size for a batch; the single-issue path
    // sends only a timeout and ignores it.
    if (handlers.onAwaitingConfirm) handlers.onAwaitingConfirm(JSON.parse(event.data).data);
  });
  stream.addEventListener("batch_queue", (event) => {
    if (handlers.onBatchQueue) handlers.onBatchQueue(JSON.parse(event.data).data);
  });
  stream.addEventListener("batch_item", (event) => {
    if (handlers.onBatchItem) handlers.onBatchItem(JSON.parse(event.data).data);
  });
  stream.addEventListener("batch_summary", (event) => {
    if (handlers.onBatchSummary) handlers.onBatchSummary(JSON.parse(event.data).data);
  });
  stream.addEventListener("error", (event) => {
    // SSE also fires a nameless 'error' on transport failure; only ours has data.
    if (!event.data) return;
    const detail = JSON.parse(event.data).data;
    appendLines(handlers.console, detail);
    if (handlers.onError) handlers.onError(detail);
  });
  stream.addEventListener("close", () => {
    stream.close();
    stream = null;
    if (handlers.onDone) handlers.onDone();
  });
  stream.addEventListener("done", () => {
    if (handlers.onDone) handlers.onDone();
  });
}

/* ------------------------------------------------------- confirmation modal */

/* Shared by both write paths. `body` lets the batch state the number of
 * projects the operator is about to approve - the single-issue wording ("o
 * relatório acima descreve exatamente o que será criado") is false for a queue
 * of 5451, where no per-item report has been rendered yet. */
function openConfirm(body) {
  $("confirm-body").textContent = body || UI.UI_CONFIRM_BODY;
  $("confirm-error").hidden = true;
  $("confirm-input").value = "";
  $("confirm-ok").disabled = true;
  $("confirm-modal").hidden = false;
  $("confirm-input").focus();
}

function closeConfirm() {
  $("confirm-modal").hidden = true;
}

$("confirm-input").addEventListener("input", (event) => {
  $("confirm-ok").disabled = !ACCEPT.has(event.target.value.trim().toLowerCase());
});

$("confirm-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !$("confirm-ok").disabled) $("confirm-ok").click();
  if (event.key === "Escape") $("confirm-cancel").click();
});

$("confirm-ok").addEventListener("click", async () => {
  const answer = $("confirm-input").value;
  try {
    await postJSON(`/api/jobs/${currentJob}/confirm`, { answer });
    closeConfirm();
  } catch (error) {
    // The server is authoritative and treats a wrong word as a cancel, exactly
    // like the CLI prompt does.
    showError($("confirm-error"), error.message);
    $("confirm-ok").disabled = true;
  }
});

$("confirm-cancel").addEventListener("click", async () => {
  closeConfirm();
  try {
    await postJSON(`/api/jobs/${currentJob}/cancel`, {});
  } catch (error) {
    /* the job may have expired on its own */
  }
});

/* -------------------------------------------------------------- report tools */

$("copy-report").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("report").textContent);
  $("copy-report").textContent = UI.UI_REPORT_COPIED;
  setTimeout(() => ($("copy-report").textContent = UI.UI_REPORT_COPY), 1500);
});

/* -------------------------------------------------------------------- audit */

$("run-audit").addEventListener("click", async () => {
  const tracker = parseInt($("tracker").value, 10);
  $("audit-error").hidden = true;
  if (!tracker || tracker <= 0) {
    showError($("audit-error"), UI.UI_TRACKER_INVALID);
    return;
  }

  $("audit-console").textContent = "";
  $("audit-console-card").hidden = false;
  $("audit-summary").hidden = true;
  $("run-audit").disabled = true;
  $("audit-spinner").hidden = false;

  try {
    const { job_id } = await postJSON("/api/audit", { tracker });
    listen(job_id, {
      console: $("audit-console"),
      onAuditResult: renderAudit,
      onError: (detail) => showError($("audit-error"), detail),
      onDone: () => {
        $("run-audit").disabled = false;
        $("audit-spinner").hidden = true;
      },
    });
  } catch (error) {
    $("run-audit").disabled = false;
    $("audit-spinner").hidden = true;
    showError($("audit-error"), error.message);
  }
});

function renderAudit(result) {
  const row = $("audit-kpi");
  row.textContent = "";
  row.appendChild(kpi("Issues lidas", result.total_issues, "tracker " + result.tracker));
  row.appendChild(kpi("Campos auditados", result.fields.length));
  row.appendChild(
    kpi(
      "Valores perdidos",
      result.missing_total,
      result.missing_total ? result.affected_issues + " preenchimento(s)" : null,
      result.missing_total ? "warning" : null
    )
  );

  const body = $("audit-table").querySelector("tbody");
  body.textContent = "";
  for (const field of result.fields) {
    for (const miss of field.missing) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", "strong", field.field_name));
      tr.appendChild(el("td", "wrap", miss.value));
      tr.appendChild(el("td", "num", miss.count));
      body.appendChild(tr);
    }
  }
  if (!body.childElementCount) {
    const tr = document.createElement("tr");
    const td = el("td", null, UI.UI_AUDIT_COMPLETE);
    td.colSpan = 3;
    tr.appendChild(td);
    body.appendChild(tr);
  }
  $("audit-summary").hidden = false;
}

/* --------------------------------------------------------------------- lote */

let batchMode = "dry";
let batchRunId = "";
/* Counts are kept here rather than read back off the table, because the table
 * is capped (see MAX_BATCH_ROWS) and the numbers must stay exact: the four
 * states adding up to the queue size is the one arithmetic the batch report
 * exists to guarantee. */
let batchCounts = { ok: 0, failed: 0, skipped: 0 };
let batchTotal = 0;

/* How many item rows the table keeps. A failure row is never dropped - the
 * reason a run went wrong is the whole point of looking - so only successes and
 * skips are trimmed, oldest first. */
const MAX_BATCH_ROWS = 400;

const BATCH_STATE_LABEL = {
  ok: () => UI.UI_BATCH_STATE_OK,
  failed: () => UI.UI_BATCH_STATE_FAILED,
  skipped: () => UI.UI_BATCH_STATE_SKIPPED,
  pending: () => UI.UI_BATCH_STATE_PENDING,
};

const BATCH_STATE_PILL = { ok: "is-ok", failed: "is-bad", skipped: "is-mono" };

$("batch-mode").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-mode]");
  if (!button) return;
  batchMode = button.dataset.mode;
  $("batch-mode").querySelectorAll("button").forEach((b) => {
    const active = b === button;
    b.classList.toggle("is-active", active);
    b.setAttribute("aria-checked", String(active));
  });
  $("apply-banner").hidden = batchMode !== "apply";
  $("run-batch").textContent =
    batchMode === "apply" ? UI.UI_BATCH_RUN_APPLY : UI.UI_BATCH_RUN;
});

$("run-batch").addEventListener("click", () => {
  const limitRaw = $("batch-limit").value.trim();
  const payload = { project: $("batch-project").value, mode: batchMode };
  if (limitRaw !== "") payload.limit = parseInt(limitRaw, 10);
  startBatch("/api/batch", payload);
});

$("batch-runs-reload").addEventListener("click", loadBatchRuns);

async function startBatch(path, payload) {
  $("batch-error").hidden = true;
  resetBatchView();
  setBatchRunning(true);

  try {
    const { job_id } = await postJSON(path, payload);
    currentJob = job_id;
    $("batch-download").href = `/api/jobs/${job_id}/report`;
    listen(job_id, {
      console: $("batch-console"),
      onBatchQueue: onBatchQueue,
      onBatchItem: onBatchItem,
      onBatchSummary: onBatchSummary,
      onAwaitingConfirm: (data) =>
        openConfirm(fill(UI.UI_BATCH_CONFIRM_BODY, { count: (data && data.count) || 0 })),
      onError: (detail) => showError($("batch-error"), detail),
      onDone: () => {
        setBatchRunning(false);
        loadBatchRuns();
      },
    });
  } catch (error) {
    setBatchRunning(false);
    showError($("batch-error"), error.message);
  }
}

function resetBatchView() {
  batchCounts = { ok: 0, failed: 0, skipped: 0 };
  batchTotal = 0;
  batchRunId = "";
  $("batch-console").textContent = "";
  $("batch-console-card").hidden = false;
  $("batch-table").querySelector("tbody").textContent = "";
  $("batch-summary-card").hidden = true;
  $("batch-progress-card").hidden = true;
  $("batch-progress-title").textContent = "";
  setBatchProgress(0, 0);
}

function setBatchRunning(running) {
  $("run-batch").disabled = running;
  $("run-batch").textContent = running
    ? UI.UI_RUNNING
    : batchMode === "apply"
    ? UI.UI_BATCH_RUN_APPLY
    : UI.UI_BATCH_RUN;
  $("batch-spinner").hidden = !running;
  document
    .querySelectorAll("#batch-runs-table button")
    .forEach((button) => (button.disabled = running));
}

function onBatchQueue(data) {
  batchTotal = data.count;
  batchRunId = data.run_id;
  $("batch-progress-card").hidden = false;
  $("batch-progress-title").textContent = data.count
    ? fill(UI.UI_BATCH_QUEUE_FOUND, { count: data.count, run_id: data.run_id })
    : UI.UI_BATCH_QUEUE_EMPTY;
  setBatchProgress(0, data.count);
  renderBatchKpis();
}

function onBatchItem(item) {
  batchCounts[item.state] = (batchCounts[item.state] || 0) + 1;
  setBatchProgress(item.position, item.total || batchTotal);
  renderBatchKpis();
  appendBatchRow(item);
}

function setBatchProgress(done, total) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  $("batch-progress-fill").style.width = percent + "%";
  $("batch-progress").setAttribute("aria-valuenow", String(percent));
  $("batch-progress").setAttribute(
    "aria-valuetext",
    fill(UI.UI_BATCH_PROGRESS, { done, total })
  );
}

function renderBatchKpis() {
  const row = $("batch-kpi");
  row.textContent = "";
  const done = batchCounts.ok + batchCounts.failed + batchCounts.skipped;
  row.appendChild(
    kpi(UI.UI_BATCH_COL_TOTAL, batchTotal, fill(UI.UI_BATCH_PROGRESS, { done, total: batchTotal }), "accent")
  );
  row.appendChild(kpi(UI.UI_BATCH_STATE_OK, batchCounts.ok));
  row.appendChild(kpi(UI.UI_BATCH_STATE_SKIPPED, batchCounts.skipped));
  row.appendChild(
    kpi(UI.UI_BATCH_STATE_FAILED, batchCounts.failed, null, batchCounts.failed ? "critical" : null)
  );
}

function appendBatchRow(item) {
  const body = $("batch-table").querySelector("tbody");
  const tr = document.createElement("tr");
  tr.dataset.state = item.state;
  tr.appendChild(el("td", "num strong", item.issue_id));

  const state = document.createElement("td");
  const label = BATCH_STATE_LABEL[item.state];
  state.appendChild(
    el("span", "pill " + (BATCH_STATE_PILL[item.state] || ""), label ? label() : item.state)
  );
  tr.appendChild(state);
  tr.appendChild(el("td", "wrap", item.detail || "—"));

  const report = document.createElement("td");
  // Only a migrated item has a report file; a skip never built a plan and a
  // failure may have died before the report was written.
  if (item.state === "ok" && batchRunId) {
    const link = el("a", "link", UI.UI_BATCH_ITEM_REPORT);
    link.href = `/api/batch/runs/${encodeURIComponent(batchRunId)}/reports/${item.issue_id}`;
    link.target = "_blank";
    link.rel = "noopener";
    report.appendChild(link);
  } else {
    report.textContent = "—";
  }
  tr.appendChild(report);
  body.appendChild(tr);
  trimBatchRows(body);
}

function trimBatchRows(body) {
  let excess = body.childElementCount - MAX_BATCH_ROWS;
  if (excess <= 0) return;
  for (const row of Array.from(body.children)) {
    if (excess <= 0) break;
    if (row.dataset.state === "failed") continue; // failures are why you look
    body.removeChild(row);
    excess -= 1;
  }
}

function onBatchSummary(data) {
  renderText($("batch-summary"), data.text);
  $("batch-summary-card").hidden = false;
  // Re-point the download at the RUN, now that the run is finished and its
  // resumo.txt exists. The job-id URL works only until this process restarts.
  if (batchRunId) $("batch-download").href = batchSummaryUrl(batchRunId);
}

function batchSummaryUrl(runId) {
  return `/api/batch/runs/${encodeURIComponent(runId)}/summary`;
}

/* Reopen a finished run. Everything here comes from the ledger and from disk,
 * so it works after a refresh, after a restart, and for runs driven from the
 * CLI before this view existed. */
async function openBatchRun(run) {
  $("batch-error").hidden = true;
  resetBatchView();
  $("batch-console-card").hidden = true; // no live console for a past run
  batchRunId = run.run_id;

  let items;
  let summary;
  try {
    items = await api(`/api/batch/runs/${encodeURIComponent(run.run_id)}/items`);
    summary = await fetch(batchSummaryUrl(run.run_id)).then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    });
  } catch (error) {
    showError($("batch-error"), error.message);
    return;
  }

  batchTotal = items.length;
  batchCounts = { ok: 0, failed: 0, skipped: 0 };
  $("batch-progress-card").hidden = false;
  $("batch-progress-title").textContent = fill(UI.UI_BATCH_VIEWING, {
    run_id: run.run_id,
    label: run.label,
  });
  for (const item of items) {
    batchCounts[item.state] = (batchCounts[item.state] || 0) + 1;
    appendBatchRow(item);
  }
  const done = batchCounts.ok + batchCounts.failed + batchCounts.skipped;
  setBatchProgress(done, batchTotal);
  renderBatchKpis();

  renderText($("batch-summary"), summary);
  $("batch-download").href = batchSummaryUrl(run.run_id);
  $("batch-summary-card").hidden = false;
}

async function loadBatchRuns() {
  const runs = await api("/api/batch/runs").catch(() => []);
  const body = $("batch-runs-table").querySelector("tbody");
  body.textContent = "";

  for (const run of runs) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "is-mono", run.run_id));
    tr.appendChild(el("td", "strong", run.label));
    tr.appendChild(el("td", null, String(run.started_at).replace("T", " ")));
    tr.appendChild(el("td", "num", run.total));
    tr.appendChild(el("td", "num", run.resumable));

    const actions = document.createElement("td");
    // Unconditional: a run with nothing left to resume is exactly the case that
    // used to render no actions at all, which is how a finished run's report
    // became unreachable after a refresh.
    const open = el("button", "btn btn-ghost", UI.UI_BATCH_OPEN);
    open.type = "button";
    open.addEventListener("click", () => openBatchRun(run));
    actions.appendChild(open);

    if (run.resumable) {
      const button = el("button", "btn btn-ghost", UI.UI_BATCH_RESUME);
      button.type = "button";
      // Resume follows the mode selected above, so retaking a run in Gravação
      // still goes through the confirmation gate.
      button.addEventListener("click", () =>
        startBatch("/api/batch/resume", { run_id: run.run_id, mode: batchMode })
      );
      actions.appendChild(button);
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  }
  $("batch-runs-empty").hidden = runs.length > 0;
}

/* ------------------------------------------------------------------ history */

let historyRows = [];

async function loadHistory() {
  historyRows = await api("/api/history").catch(() => []);
  drawHistory();
}

function drawHistory() {
  const term = $("history-filter").value.trim().toLowerCase();
  const rows = term
    ? historyRows.filter((row) =>
        [row.redmine_id, row.glpi_id, row.glpi_itemtype, row.status, row.migrated_at]
          .join(" ")
          .toLowerCase()
          .includes(term)
      )
    : historyRows;

  const body = $("history-table").querySelector("tbody");
  body.textContent = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "num strong", row.redmine_id));
    tr.appendChild(el("td", "num strong", row.glpi_id));
    const type = document.createElement("td");
    type.appendChild(el("span", "pill is-mono", row.glpi_itemtype));
    tr.appendChild(type);
    tr.appendChild(el("td", "num", row.parent_redmine_id ?? "—"));
    const status = document.createElement("td");
    status.appendChild(el("span", "pill " + (row.status === "ok" ? "is-ok" : "is-bad"), row.status));
    tr.appendChild(status);
    tr.appendChild(el("td", null, row.migrated_at.replace("T", " ")));
    body.appendChild(tr);
  }
  $("history-empty").hidden = rows.length > 0;
}

$("history-filter").addEventListener("input", drawHistory);
$("history-reload").addEventListener("click", loadHistory);

/* ------------------------------------------------------------------- config */

let configLoaded = false;

async function loadConfig() {
  if (configLoaded) return;
  let data;
  try {
    data = await api("/api/config");
  } catch (error) {
    // Swallowing this used to leave the tab simply blank, which reads as "there
    // is no configuration" rather than "the panel could not read it".
    showError($("config-error"), fill(UI.UI_CONFIG_ERROR, { detail: error.message }));
    return;
  }
  $("config-error").hidden = true;
  configLoaded = true;

  const root = $("config-body");
  root.textContent = "";

  // First card, deliberately: a container id or an entity id below means
  // nothing until you know which instance answered. TEST and PRODUCTION number
  // the same containers differently (15/26 there, 17/18 here).
  root.appendChild(
    kvCard(
      UI.UI_CONFIG_INSTANCE,
      [
        [UI.UI_CONFIG_INSTANCE_GLPI, data.instance.glpi],
        [UI.UI_CONFIG_INSTANCE_REDMINE, data.instance.redmine],
      ],
      UI.UI_CONFIG_INSTANCE_INTRO
    )
  );

  root.appendChild(
    kvCard(
      UI.UI_CONFIG_ENV,
      data.env.map((item) => [
        item.name,
        item.present ? UI.UI_CONFIG_ENV_PRESENT : UI.UI_CONFIG_ENV_MISSING,
      ]),
      UI.UI_CONFIG_ENV_INTRO
    )
  );

  const scope = data.scope;
  const types = Object.entries(scope.projecttasktypes || {})
    .map(([tracker, type]) => `${tracker} → ${type}`)
    .join(", ");
  root.appendChild(
    kvCard(UI.UI_CONFIG_SCOPE, [
      ["Container campos adicionais", `${scope.container_additional_fields} — ${scope.itemtype_additional_fields}`],
      ["Container Faturamento", `${scope.container_faturamento} — ${scope.itemtype_faturamento}`],
      ["Container tarefas (nunca gravado)", data.constants.container_task_additional_fields],
      ["Tracker Faturamento", scope.tracker_faturamento],
      ["Tracker Atividades", scope.tracker_atividades],
      ["Trackers aceitos como raiz", scope.root_trackers.join(", ")],
      ["Trackers aceitos como tarefa", scope.task_trackers.join(", ")],
      ["Tracker → tipo de tarefa", types || UI.UI_EMPTY],
      ["Colunas obrigatórias (container do projeto)", scope.mandatory_columns.join(", ")],
      // Empty since 2026-08-07, and saying so is the point: the block vanishing
      // from the report is a fact about GLPI's flags, not a rendering gap.
      ["Colunas obrigatórias (container Faturamento)",
        scope.mandatory_columns_container26.join(", ") || UI.UI_EMPTY],
      ["Banco local", scope.db_path],
      ["Diretório de relatórios", scope.reports_dir],
    ])
  );

  const constants = data.constants;
  root.appendChild(
    kvCard(UI.UI_CONFIG_CONSTANTS, [
      ["Entidade padrão (Cliente sem entidade)", constants.default_entity_id],
      ["Ajuste de fuso Redmine → GLPI (horas)", constants.utc_offset_hours],
      ["Tamanho máximo de anexo (MB, limite do GLPI)", constants.document_max_size_mb],
      ["Corte de texto em campos do plugin (caracteres)", constants.plugin_text_max_length],
    ])
  );

  root.appendChild(
    kvCard(
      UI.UI_CONFIG_BATCH_PROJECTS,
      (data.batch_projects || []).map((project) => [
        project.id,
        "tracker " + project.tracker,
      ])
    )
  );

  for (const [section, rows] of Object.entries(data.mapping)) {
    root.appendChild(mappingCard(section, rows));
  }

  root.appendChild(
    kvCard(
      UI.UI_CONFIG_NEVER_WRITE,
      data.never_write.map((item) => [item.column, item.reason || "—"])
    )
  );

  const statuses = data.status_map.statuses || data.status_map;
  root.appendChild(
    kvCard(
      UI.UI_CONFIG_STATUS_MAP,
      Object.entries(statuses).map(([key, value]) => [
        `${key} — ${(value && value.label) || ""}`.trim(),
        value && typeof value === "object" ? "GLPI " + value.glpi_id : value,
      ])
    )
  );

  const users = data.user_map.users || data.user_map;
  root.appendChild(
    kvCard(
      UI.UI_CONFIG_USER_MAP,
      Object.entries(users).map(([key, value]) => [
        "RDM " + key,
        typeof value === "object"
          ? `${value.login || "?"} → GLPI ${value.glpi_id === null ? "(não mapeado)" : value.glpi_id}`
          : value,
      ])
    )
  );

  root.appendChild(entityCard(data.entity_map || {}));
}

/* The map that decides where every project lands. Listed client-first, because
 * "where does THIS cliente go" is the question being asked - the YAML file is
 * entity-first, which is the wrong index for reading. */
function entityCard(entityMap) {
  const wrapper = el("section", "config-section");
  wrapper.appendChild(el("h3", null, UI.UI_CONFIG_ENTITY_MAP));
  const card = el("div", "card");
  card.appendChild(el("p", "empty", UI.UI_CONFIG_ENTITY_MAP_INTRO));

  const wrap = el("div", "table-wrap");
  const table = el("table", "data");
  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  for (const label of ["Cliente (Redmine)", "Entidade (GLPI)", UI.UI_CONFIG_ENTITY_MAP_ID_TESTE]) {
    head.appendChild(el("th", null, label));
  }
  thead.appendChild(head);
  table.appendChild(thead);

  const body = document.createElement("tbody");
  for (const row of entityMap.clients || []) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "strong", row.client));
    tr.appendChild(el("td", "wrap", row.entity));
    tr.appendChild(el("td", "num", row.id_teste === null ? "—" : row.id_teste));
    body.appendChild(tr);
  }
  // Names the sheet withholds on purpose. Shown as their own rows rather than
  // simply left out: "withheld" and "not yet mapped" are different facts, and
  // both send the project to the default entity.
  for (const name of entityMap.nao_sera_migrado || []) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "strong", name));
    const cell = el("td", "wrap");
    cell.appendChild(el("span", "pill is-mono", "não será migrado"));
    tr.appendChild(cell);
    tr.appendChild(el("td", "num", "—"));
    body.appendChild(tr);
  }
  if (!body.childElementCount) {
    const tr = document.createElement("tr");
    const td = el("td", null, UI.UI_EMPTY);
    td.colSpan = 3;
    tr.appendChild(td);
    body.appendChild(tr);
  }
  table.appendChild(body);
  wrap.appendChild(table);
  card.appendChild(wrap);
  wrapper.appendChild(card);
  return wrapper;
}

$("config-reload").addEventListener("click", () => {
  configLoaded = false;
  loadConfig();
});

function kvCard(title, pairs, intro) {
  const section = el("section", "config-section");
  section.appendChild(el("h3", null, title));
  const card = el("div", "card");
  if (intro) card.appendChild(el("p", "empty", intro));
  const grid = el("div", "kv");
  for (const [key, value] of pairs) {
    const row = document.createElement("div");
    row.appendChild(el("span", "k", key));
    row.appendChild(el("span", "v", value === null || value === undefined ? "—" : value));
    grid.appendChild(row);
  }
  if (!pairs.length) grid.appendChild(el("span", "k", UI.UI_EMPTY));
  card.appendChild(grid);
  section.appendChild(card);
  return section;
}

function mappingCard(section, rows) {
  const wrapper = el("section", "config-section");
  wrapper.appendChild(el("h3", null, `${UI.UI_CONFIG_MAPPING} — ${section}`));
  const card = el("div", "card");
  const wrap = el("div", "table-wrap");
  const table = el("table", "data");

  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  for (const label of ["Coluna GLPI", "Origem no Redmine", "Conversão", "Dicionário", "Obrig."]) {
    head.appendChild(el("th", null, label));
  }
  thead.appendChild(head);
  table.appendChild(thead);

  const body = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", "strong", row.column));
    tr.appendChild(el("td", "wrap", row.sources.join(" | ") || "—"));
    const transform = document.createElement("td");
    transform.appendChild(el("span", "pill is-mono", row.transform));
    tr.appendChild(transform);
    tr.appendChild(el("td", null, row.itemtype || "—"));
    tr.appendChild(el("td", null, row.mandatory ? "sim" : "—"));
    body.appendChild(tr);
  }
  table.appendChild(body);
  wrap.appendChild(table);
  card.appendChild(wrap);
  wrapper.appendChild(card);
  return wrapper;
}

/* --------------------------------------------------------------------- boot */

checkHealth();
$("issue").focus();

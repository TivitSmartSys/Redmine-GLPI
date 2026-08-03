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
    if (button.dataset.view === "history") loadHistory();
    if (button.dataset.view === "config") loadConfig();
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
      onAwaitingConfirm: openConfirm,
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

  const warnings = [
    [summary.unresolved, UI.UI_WARN_UNRESOLVED, "warning"],
    [summary.missing_mandatory, UI.UI_WARN_MANDATORY, "warning"],
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
  stream.addEventListener("awaiting_confirm", () => {
    if (handlers.onAwaitingConfirm) handlers.onAwaitingConfirm();
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

function openConfirm() {
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
  const data = await api("/api/config").catch(() => null);
  if (!data) return;
  configLoaded = true;

  const root = $("config-body");
  root.textContent = "";

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
  root.appendChild(
    kvCard(UI.UI_CONFIG_SCOPE, [
      ["Container campos adicionais", `${scope.container_additional_fields} — ${scope.itemtype_additional_fields}`],
      ["Container Faturamento", `${scope.container_faturamento} — ${scope.itemtype_faturamento}`],
      ["Tracker Faturamento", scope.tracker_faturamento],
      ["Trackers aceitos como raiz", scope.root_trackers.join(", ")],
      ["Trackers aceitos como tarefa", scope.task_trackers.join(", ")],
      ["Colunas obrigatórias", scope.mandatory_columns.join(", ")],
      ["Banco local", scope.db_path],
    ])
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
}

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

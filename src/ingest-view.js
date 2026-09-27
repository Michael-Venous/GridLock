// Local-only reviewed update path. The browser never receives AWS credentials or parser code.
const value = id => document.getElementById(id).value.trim();
const el = id => document.getElementById(id);
const POLL_MS = 1400;

export function ingestPayload(fields) {
  const mode = fields.mode === "find" ? "find" : "url";
  const source = String(fields.source ?? "").trim();
  if (!source) throw new Error(mode === "find" ? "Enter a utility name to search for its filing." : "Enter a public PDF or ZIP URL.");
  if (mode === "url") {
    let url;
    try { url = new URL(source); } catch { throw new Error("Enter a complete public URL beginning with http:// or https://."); }
    if (!["http:", "https:"].includes(url.protocol)) throw new Error("Enter a public HTTP or HTTPS URL.");
  }
  const payload = { mode, source };
  if (mode === "url" && fields.company?.trim()) payload.company = fields.company.trim();
  for (const key of ["date", "edition", "title", ...(mode === "url" ? ["zipMember"] : [])]) {
    const entry = String(fields[key] ?? "").trim();
    if (entry) payload[key] = entry;
  }
  return payload;
}

export function previewFacts(preview) {
  if (!preview || typeof preview !== "object") return [];
  return [
    ["Filing", preview.title],
    ["Utility / plan", typeof preview.plan === "object" ? preview.plan.name : preview.plan],
    ["Edition", preview.edition],
    ["Filing date", preview.date],
    ["Date source", preview.dateBasis],
    ["Parser", preview.parser],
    ["Detection", preview.route],
    ["Project records", preview.records],
    ["With target dates", preview.dated],
    ["With project IDs", preview.withId],
    ["PDF SHA-256", preview.sha256],
  ].filter(([, entry]) => entry !== undefined && entry !== null && entry !== "");
}

export function previewSamples(preview) {
  return (Array.isArray(preview?.samples) ? preview.samples : []).slice(0, 5).map(record => ({
    id: String(record?.projectId ?? "—"),
    name: String(record?.name ?? "—"),
    date: record?.inService ? String(record.inService) : record?.hasInServiceDate === true ? "Date present" : "—",
  }));
}

async function api(path, options) {
  const response = await fetch(path, { ...options, headers: options?.body ? { "Content-Type": "application/json" } : undefined });
  let body;
  try { body = await response.json(); } catch { throw new Error(`Local ingest service returned HTTP ${response.status} without JSON.`); }
  if (!response.ok) throw new Error(body.error || body.message || `HTTP ${response.status}`);
  return body;
}

function safeWebUrl(address) {
  try {
    const parsed = new URL(address);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch { return null; }
}

export function createIngestView({ onBack }) {
  const form = el("ingest-form"), previewButton = el("ingest-preview"), registerButton = el("ingest-register");
  const rebuildButton = el("ingest-rebuild");
  const controls = [...form.querySelectorAll("input")];
  let available = false, aiAvailable = false, currentJob = null, previewKey = null, pollTimer = null, polling = false;

  function payloadFromForm() {
    const mode = form.querySelector('input[name="ingest-mode"]:checked').value;
    return ingestPayload({
      mode, source: mode === "find" ? value("ingest-find") : value("ingest-url"),
      company: value("ingest-company"), date: value("ingest-date"), edition: value("ingest-edition"),
      title: value("ingest-title"), zipMember: value("ingest-zip-member"),
    });
  }

  function showError(message) { el("ingest-form-error").textContent = message; el("ingest-form-error").hidden = !message; }
  function setProgress(message, kind = "") {
    el("ingest-progress").textContent = message;
    el("ingest-progress").dataset.kind = kind;
  }
  function busy(on) {
    controls.forEach(control => { control.disabled = on || (control.name === "ingest-mode" && control.value === "find" && !aiAvailable); });
    previewButton.disabled = on || !available;
    registerButton.disabled = on;
  }
  function switchMode() {
    const find = form.querySelector('input[name="ingest-mode"]:checked').value === "find";
    el("ingest-find-field").hidden = !find;
    el("ingest-url-field").hidden = find;
    el("ingest-company-field").hidden = find;
    showError("");
  }
  function clearPreview() {
    currentJob = null; previewKey = null;
    el("ingest-preview-result").hidden = true;
    el("ingest-preview-result").replaceChildren();
    el("ingest-log-wrap").hidden = true;
    el("ingest-register-wrap").hidden = true;
    el("ingest-rebuild-wrap").hidden = true;
    el("ingest-complete").hidden = true;
    el("ingest-logs").textContent = "";
  }
  function renderPreview(preview) {
    const container = el("ingest-preview-result");
    container.replaceChildren();
    const dl = document.createElement("dl"); dl.className = "ingest-facts";
    for (const [label, entry] of previewFacts(preview)) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = String(entry); dl.append(dt, dd);
    }
    container.append(dl);
    const samples = previewSamples(preview);
    if (samples.length) {
      const section = document.createElement("section"), heading = document.createElement("h3"), table = document.createElement("table");
      section.className = "ingest-samples"; heading.textContent = `Sample extracted records (${samples.length})`;
      table.className = "ingest-samples-table";
      const thead = document.createElement("thead"), headrow = document.createElement("tr");
      for (const label of ["ID", "Project", "In service"]) { const th = document.createElement("th"); th.textContent = label; headrow.append(th); }
      thead.append(headrow); table.append(thead);
      const tbody = document.createElement("tbody");
      for (const sample of samples) {
        const tr = document.createElement("tr");
        for (const item of [sample.id, sample.name, sample.date]) { const td = document.createElement("td"); td.textContent = item; tr.append(td); }
        tbody.append(tr);
      }
      table.append(tbody); section.append(heading, table); container.append(section);
    }
    if (preview?.recordsAvailable && currentJob) {
      const p = document.createElement("p"), a = document.createElement("a");
      a.href = `/api/ingest/jobs/${encodeURIComponent(currentJob)}/records`;
      a.download = "gridlock-ingest-preview.json";
      a.textContent = "Download all extracted records (JSON)";
      p.append(a); container.append(p);
    }
    const sourceUrl = safeWebUrl(preview?.sourceUrl);
    if (sourceUrl) {
      const p = document.createElement("p"), a = document.createElement("a");
      a.href = sourceUrl; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = "Open source filing to check records";
      p.append(a); container.append(p);
    }
    const issues = Array.isArray(preview?.issues) ? [...preview.issues] : [];
    if (preview?.dateBasis === "date ingested (dry run)" && !issues.some(issue => /filing date|--date/i.test(String(issue))))
      issues.push("The date is provisional. Enter the filing date under Document details and preview again before registering.");
    if (issues.length) {
      const group = document.createElement("div"), title = document.createElement("h3"), list = document.createElement("ul");
      group.className = "ingest-issues"; title.textContent = `${issues.length} validation note${issues.length === 1 ? "" : "s"}`;
      for (const issue of issues) { const li = document.createElement("li"); li.textContent = String(issue); list.append(li); }
      group.append(title, list); container.append(group);
    }
    const warnings = Array.isArray(preview?.warnings) ? preview.warnings : [];
    if (warnings.length) {
      const group = document.createElement("div"), title = document.createElement("h3"), list = document.createElement("ul");
      group.className = "ingest-issues";
      title.textContent = `${preview.warningCount ?? warnings.length} parser note${(preview.warningCount ?? warnings.length) === 1 ? "" : "s"} to review`;
      for (const warning of warnings) { const li = document.createElement("li"); li.textContent = String(warning); list.append(li); }
      group.append(title, list); container.append(group);
    }
    container.hidden = false;
  }
  function renderJob(job) {
    const logs = Array.isArray(job.logs) ? job.logs : [];
    el("ingest-logs").textContent = logs.slice(-150).join("\n");
    el("ingest-log-wrap").hidden = !logs.length;
    if (job.preview) renderPreview(job.preview);
    const stage = job.stage ? ` · ${job.stage}` : "";
    if (job.status === "queued" || job.status === "running") {
      setProgress(`Previewing filing${stage}. This may take several minutes.`, "working");
      busy(true);
    } else if (job.status === "succeeded") {
      const needsDate = job.preview?.dateBasis === "date ingested (dry run)";
      const duplicate = job.preview?.alreadyRegistered === true;
      const blocked = Array.isArray(job.preview?.issues) && job.preview.issues.length > 0;
      if (needsDate) form.querySelector(".ingest-options").open = true;
      setProgress(duplicate ? "This PDF is already registered; the dataset has not changed." : needsDate ? "Preview complete, but the filing date needs to be supplied. Open Document details, enter the date, and preview again." : blocked ? "Preview complete with issues. Resolve them and preview again before registering." : `Preview complete${stage}. Review the PDF and extracted counts before registering.`, duplicate || needsDate || blocked ? "" : "success");
      busy(false);
      el("ingest-register-wrap").hidden = !job.preview || needsDate || duplicate || blocked;
    } else if (job.status === "registering") {
      setProgress(`Registering and rebuilding${stage}. Keep this page open.`, "working");
      busy(true);
      el("ingest-register-wrap").hidden = false;
    } else if (job.status === "rebuilding") {
      setProgress("Rebuilding the project and change data…", "working");
      busy(true);
      el("ingest-rebuild-wrap").hidden = false;
      rebuildButton.disabled = true;
    } else if (job.status === "needs_rebuild") {
      setProgress(job.error || "Filing registered, but the dataset rebuild is incomplete.", "error");
      busy(false);
      el("ingest-register-wrap").hidden = true;
      el("ingest-rebuild-wrap").hidden = false;
      rebuildButton.disabled = false;
    } else if (job.status === "registered") {
      setProgress("Filing registered and local dataset rebuilt.", "success");
      busy(false);
      el("ingest-register-wrap").hidden = true;
      el("ingest-rebuild-wrap").hidden = true;
      el("ingest-complete").hidden = false;
    } else if (job.status === "failed") {
      setProgress(`Run stopped: ${job.error || "Unknown error"}`, "error");
      busy(false);
      el("ingest-register-wrap").hidden = true;
    }
  }
  async function poll() {
    if (!currentJob || polling) return;
    const id = currentJob;
    polling = true;
    try {
      const job = await api(`/api/ingest/jobs/${encodeURIComponent(id)}`);
      if (id !== currentJob) return;
      if (job.id !== id) throw new Error("The ingest service returned a different job.");
      renderJob(job);
      if (["queued", "running", "registering", "rebuilding"].includes(job.status)) pollTimer = setTimeout(poll, POLL_MS);
    } catch (error) {
      setProgress(`Could not check ingest progress: ${error.message}`, "error");
      busy(false);
    } finally { polling = false; }
  }
  async function checkService() {
    try {
      const status = await api("/api/ingest/status");
      available = status.available === true; aiAvailable = status.aiAvailable === true;
      el("ingest-service").textContent = !available ? (status.reason || "Local ingest service is unavailable. Start python3 local_server.py --port 8765 and reload.")
        : aiAvailable ? `Local ingest service ready · AI discovery can be attempted${status.aiReason ? `: ${status.aiReason}` : ""}` : `Local ingest service ready · AI discovery unavailable${status.aiReason ? `: ${status.aiReason}` : ""}`;
      el("ingest-service").dataset.kind = available ? (aiAvailable ? "ready" : "partial") : "error";
    } catch {
      available = false; aiAvailable = false;
      el("ingest-service").textContent = "Start python3 local_server.py --port 8765 to preview and register filings locally.";
      el("ingest-service").dataset.kind = "error";
    }
    if (!aiAvailable && form.querySelector('input[name="ingest-mode"]:checked').value === "find") form.querySelector('input[name="ingest-mode"][value="url"]').checked = true;
    switchMode(); busy(false);
  }

  form.addEventListener("change", event => {
    if (event.target.name === "ingest-mode") switchMode();
    if (previewKey !== null) {
      el("ingest-register-wrap").hidden = true;
      setProgress("Source details changed. Preview again before registering.");
    }
  });
  form.addEventListener("input", () => {
    if (previewKey !== null) {
      el("ingest-register-wrap").hidden = true;
      setProgress("Source details changed. Preview again before registering.");
    }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    let payload;
    try { payload = payloadFromForm(); } catch (error) { showError(error.message); return; }
    if (!available) { showError("Start the local ingest service before previewing."); return; }
    if (payload.mode === "find" && !aiAvailable) { showError("AI discovery is unavailable on this server."); return; }
    if (pollTimer) clearTimeout(pollTimer);
    clearPreview(); showError(""); busy(true); setProgress("Starting preview…", "working");
    try {
      const job = await api("/api/ingest/jobs", { method: "POST", body: JSON.stringify(payload) });
      if (!job.id) throw new Error("The ingest service did not return a job ID.");
      currentJob = job.id; previewKey = JSON.stringify(payload);
      renderJob(job);
      poll();
    } catch (error) { setProgress(`Preview could not start: ${error.message}`, "error"); busy(false); }
  });
  registerButton.addEventListener("click", async () => {
    if (!currentJob || !previewKey) return;
    try {
      if (JSON.stringify(payloadFromForm()) !== previewKey) throw new Error("Source details changed. Preview again before registering.");
      registerButton.disabled = true; setProgress("Starting registration and rebuild…", "working");
      const job = await api(`/api/ingest/jobs/${encodeURIComponent(currentJob)}/register`, { method: "POST", body: "{}" });
      renderJob(job); poll();
    } catch (error) { setProgress(`Registration could not start: ${error.message}`, "error"); registerButton.disabled = false; }
  });
  rebuildButton.addEventListener("click", async () => {
    if (!currentJob) return;
    try {
      rebuildButton.disabled = true;
      setProgress("Retrying the dataset rebuild…", "working");
      const job = await api(`/api/ingest/jobs/${encodeURIComponent(currentJob)}/rebuild`, { method: "POST", body: "{}" });
      renderJob(job); poll();
    } catch (error) {
      setProgress(`Rebuild could not start: ${error.message}`, "error");
      rebuildButton.disabled = false;
    }
  });
  el("ingest-back").addEventListener("click", onBack);
  el("ingest-reload").addEventListener("click", () => window.location.reload());
  switchMode();
  return { open: checkService };
}

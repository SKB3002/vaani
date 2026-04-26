// Google Sheets backup — Settings panel wiring (v4, UI-driven setup).
(function () {
  const panel = document.getElementById("sheets-panel");
  if (!panel) return;

  const chip = document.getElementById("sheets-status-chip");
  const queueLabel = document.getElementById("sheets-queue-label");
  const errBox = document.getElementById("sheets-last-error");
  const reportBox = document.getElementById("sheets-reconcile-report");

  const credChip = document.getElementById("sheets-cred-chip");
  const clientEmail = document.getElementById("sheets-client-email");
  const uploadBtn = document.getElementById("sheets-upload-btn");
  const replaceBtn = document.getElementById("sheets-replace-btn");
  const removeBtn = document.getElementById("sheets-remove-btn");
  const fileInput = document.getElementById("sheets-file-input");

  const spreadsheetForm = document.getElementById("sheets-spreadsheet-form");
  const spreadsheetInput = document.getElementById("sheets-spreadsheet-input");
  const detectedId = document.getElementById("sheets-detected-id");

  const toggle = document.getElementById("sheets-enable-toggle");
  const enableLabel = document.getElementById("sheets-enable-label");
  const enableHint = document.getElementById("sheets-enable-hint");
  const enableChipEl = document.getElementById("sheets-enable-chip");

  const testBtn = document.getElementById("sheets-test-btn");
  const syncBtn = document.getElementById("sheets-sync-btn");
  const reconcileBtn = document.getElementById("sheets-reconcile-btn");
  const drainBtn = document.getElementById("sheets-drain-btn");

  let timer = null;
  let lastStatus = { enabled: false, credentials_uploaded: false };

  function toast(msg, kind) {
    const t = window.FinEye && typeof window.FinEye.toast === "function" ? window.FinEye.toast : null;
    if (t) t({ type: kind || "info", message: String(msg) });
    else console.info(msg);
  }

  function requireEnabled() {
    if (!lastStatus.enabled) {
      toast("Sheets sync is not enabled. Complete setup above first.", "warning");
      return false;
    }
    return true;
  }

  function renderStatusChip(s) {
    if (!s.credentials_uploaded) {
      chip.textContent = "No credentials";
      chip.dataset.state = "disabled";
      return;
    }
    if (!s.spreadsheet_id) {
      chip.textContent = "Credentials ready — set spreadsheet";
      chip.dataset.state = "disabled";
      return;
    }
    if (!s.enabled) {
      chip.textContent = "Ready — toggle Enable to connect";
      chip.dataset.state = "disabled";
      return;
    }
    if (s.connected) {
      chip.textContent = "Connected ✓";
      chip.dataset.state = "ok";
      return;
    }
    chip.textContent = s.last_error ? `Error: ${s.last_error}` : "Error: not connected";
    chip.dataset.state = "err";
  }

  function renderCredentialsRow(s) {
    if (s.credentials_uploaded && s.client_email) {
      credChip.hidden = false;
      clientEmail.textContent = s.client_email;
      uploadBtn.hidden = true;
      replaceBtn.hidden = false;
      removeBtn.hidden = false;
    } else {
      credChip.hidden = true;
      clientEmail.textContent = "";
      uploadBtn.hidden = false;
      replaceBtn.hidden = true;
      removeBtn.hidden = true;
    }
  }

  function renderSpreadsheetRow(s) {
    if (s.spreadsheet_id) {
      detectedId.textContent = s.spreadsheet_id;
      detectedId.dataset.empty = "false";
      if (!document.activeElement || document.activeElement !== spreadsheetInput) {
        spreadsheetInput.value = s.spreadsheet_id;
      }
    } else {
      detectedId.textContent = "—";
      detectedId.dataset.empty = "true";
    }
  }

  function renderToggleRow(s) {
    const canEnable = s.credentials_uploaded && !!s.spreadsheet_id;
    toggle.disabled = !canEnable;
    enableChipEl.style.opacity = canEnable ? "1" : "0.6";
    enableChipEl.title = canEnable ? "" : "Upload credentials and set a spreadsheet first";
    toggle.checked = !!s.enabled;
    enableLabel.textContent = s.enabled ? "Enabled" : "Disabled";
    if (!canEnable) {
      enableHint.textContent = "Upload credentials and set a spreadsheet first.";
    } else if (s.enabled && s.connected) {
      enableHint.textContent = "Sync is active. Rows mirror to Google Sheets as they are written.";
    } else if (s.enabled && !s.connected) {
      enableHint.textContent = s.last_error
        ? `Not connected yet: ${s.last_error}`
        : "Enabled but not connected yet.";
    } else {
      enableHint.textContent = "Toggle on to start mirroring writes to Google Sheets.";
    }
  }

  function renderQueue(s) {
    const parts = [];
    if (s.queue_depth > 0) parts.push(`Queue: ${s.queue_depth}`);
    if (s.deadletter_count > 0) parts.push(`Deadletter: ${s.deadletter_count}`);
    if (s.last_sync_at) parts.push(`Last: ${s.last_sync_at.slice(0, 19)}`);
    queueLabel.textContent = parts.join(" · ");
    errBox.textContent = s.last_error && s.enabled ? `Error: ${s.last_error}` : "";
  }

  function render(s) {
    lastStatus = s;
    renderCredentialsRow(s);
    renderSpreadsheetRow(s);
    renderToggleRow(s);
    renderStatusChip(s);
    renderQueue(s);
  }

  async function fetchStatus() {
    try {
      const r = await fetch("/api/sheets/status");
      if (!r.ok) return null;
      const s = await r.json();
      render(s);
      return s;
    } catch (e) {
      return null;
    }
  }

  // ---------- Credentials ----------
  function openFilePicker() {
    fileInput.value = "";
    fileInput.click();
  }

  async function uploadCredentials(file) {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch("/api/sheets/credentials", { method: "POST", body: form });
    const body = await r.json().catch(() => ({}));
    if (r.ok && body.ok) {
      toast(`Credentials saved. ${body.hint || ""}`, "success");
    } else {
      toast(`Upload failed: ${body.detail || body.error || r.status}`, "danger");
    }
    fetchStatus();
  }

  async function removeCredentials() {
    if (!window.confirm("Remove uploaded credentials? Sync will be disabled.")) return;
    const r = await fetch("/api/sheets/credentials", { method: "DELETE" });
    const body = await r.json().catch(() => ({}));
    if (r.ok) toast("Credentials removed", "success");
    else toast(`Remove failed: ${body.detail || r.status}`, "danger");
    fetchStatus();
  }

  // ---------- Spreadsheet ----------
  async function saveSpreadsheet(e) {
    e.preventDefault();
    const raw = spreadsheetInput.value.trim();
    if (!raw) {
      toast("Enter a spreadsheet URL or ID first.", "warning");
      return;
    }
    const r = await fetch("/api/sheets/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spreadsheet_url: raw }),
    });
    const body = await r.json().catch(() => ({}));
    if (r.ok) {
      toast(`Spreadsheet saved: ${body.spreadsheet_id}`, "success");
    } else {
      toast(`Save failed: ${body.detail || r.status}`, "danger");
    }
    fetchStatus();
  }

  // ---------- Enable toggle ----------
  async function toggleEnabled(e) {
    const next = !!toggle.checked;
    if (next && (!lastStatus.credentials_uploaded || !lastStatus.spreadsheet_id)) {
      toggle.checked = false;
      toast("Upload credentials and set a spreadsheet first.", "warning");
      return;
    }
    toast(next ? "Enabling sync…" : "Disabling sync…");
    const r = await fetch("/api/sheets/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      toast(`Toggle failed: ${body.detail || r.status}`, "danger");
      fetchStatus();
      return;
    }
    // Poll briefly for connected=true (up to 5s).
    let connectedSeen = false;
    for (let i = 0; i < 5; i++) {
      const s = await fetchStatus();
      if (s && s.connected) {
        connectedSeen = true;
        break;
      }
      if (!next) break; // nothing to wait for when disabling
      await new Promise((res) => setTimeout(res, 1000));
    }
    if (next && connectedSeen) toast("Connected to Google Sheets", "success");
    else if (next) toast("Enabled but not connected yet — check error below", "warning");
    else toast("Sync disabled", "success");
  }

  // ---------- Action buttons ----------
  async function testConn() {
    if (!requireEnabled()) return;
    const r = await fetch("/api/sheets/test-connection", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (r.ok && body.ok) {
      toast(`Connected to "${body.spreadsheet_title}" (${(body.tabs_present || []).length} tabs)`, "success");
    } else {
      toast(`Connection failed: ${body.error || body.detail || r.status}`, "danger");
    }
    fetchStatus();
  }

  async function syncAll() {
    if (!requireEnabled()) return;
    toast("Full sync starting…");
    const r = await fetch("/api/sheets/sync-all", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (r.ok) {
      const tabs = Object.keys(body.tabs || {}).length;
      toast(`Synced ${tabs} tabs`, "success");
    } else {
      toast(`Sync failed: ${body.detail || r.status}`, "danger");
    }
    fetchStatus();
  }

  async function reconcile() {
    if (!requireEnabled()) return;
    const r = await fetch("/api/sheets/reconcile", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(`Reconcile failed: ${body.detail || r.status}`, "danger");
      return;
    }
    const unknowns = body.unknowns || {};
    const totals = Object.entries(unknowns).map(([t, rows]) => `${t}: ${rows.length}`);
    if (totals.length === 0) {
      reportBox.textContent = "No unknown rows. Sheet is in sync.";
    } else {
      reportBox.innerHTML =
        "Unknown rows in Sheet: " +
        totals.join(", ") +
        ". Use <span class='mono'>POST /api/sheets/reconcile/import?tab=X</span> to pull.";
    }
  }

  async function drain() {
    if (!requireEnabled()) return;
    const r = await fetch("/api/sheets/drain-queue", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (r.ok) {
      toast(`Drain: ${body.processed} processed, ${body.failed} failed, ${body.remaining} remaining`, "success");
    } else {
      toast(`Drain failed: ${body.detail || r.status}`, "danger");
    }
    fetchStatus();
  }

  // ---------- Wire up ----------
  if (uploadBtn) uploadBtn.addEventListener("click", openFilePicker);
  if (replaceBtn) replaceBtn.addEventListener("click", openFilePicker);
  if (removeBtn) removeBtn.addEventListener("click", removeCredentials);
  if (fileInput) {
    fileInput.addEventListener("change", () => {
      const f = fileInput.files && fileInput.files[0];
      if (f) uploadCredentials(f);
    });
  }
  if (spreadsheetForm) spreadsheetForm.addEventListener("submit", saveSpreadsheet);
  if (toggle) toggle.addEventListener("change", toggleEnabled);
  if (testBtn) testBtn.addEventListener("click", testConn);
  if (syncBtn) syncBtn.addEventListener("click", syncAll);
  if (reconcileBtn) reconcileBtn.addEventListener("click", reconcile);
  if (drainBtn) drainBtn.addEventListener("click", drain);

  function startPolling() {
    stopPolling();
    fetchStatus();
    timer = setInterval(() => {
      if (!document.hidden) fetchStatus();
    }, 15000);
  }
  function stopPolling() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPolling();
    else startPolling();
  });
  startPolling();
})();

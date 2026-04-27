/* =====================================================================
   Vaani — import_wizard.js
   4-step Excel/CSV import wizard. Calls /api/import/*.
   ===================================================================== */
(function () {
  "use strict";

  const TARGET_SCHEMAS = {
    expenses: ["date", "expense_name", "type_category", "payment_method", "amount",
               "person_name", "paid_for_someone", "paid_by_someone", "notes", "custom_tag"],
    investments: ["month", "long_term", "mid_long_term", "emergency_fund",
                  "bike_savings_wants", "misc_spend_save", "fixed_deposits"],
    wishlist: ["item", "target_amount", "saved_so_far", "priority", "notes", "link"],
    goals_a: ["goal_id", "goal_name", "target_amount", "current_amount", "monthly_contribution"],
    goals_b: ["goal_id", "goal_name", "target_amount", "manual_saved", "auto_added", "monthly_contribution"],
  };

  const state = {
    step: 1,
    uploadId: null,
    target: "expenses",
    presetId: "",
    dateFormat: "",
    sheet: null,
    sheets: [],
    columns: [],
    preview: [],
    mapping: {},
    dryRun: null,
    lastCommit: null,
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function setStep(n) {
    state.step = n;
    $$(".step").forEach((s, i) => {
      const idx = i + 1;
      s.setAttribute("aria-current", idx === n ? "step" : "false");
      s.setAttribute("data-done", idx < n ? "true" : "false");
    });
    $$("[data-wizard-step]").forEach(p => {
      p.hidden = Number(p.dataset.wizardStep) !== n;
    });
  }

  // Load presets on page init
  async function loadPresets() {
    const sel = $("#import-preset");
    if (!sel) return;
    try {
      const data = await window.Vaani.api("/api/import/presets");
      const presets = (data && data.presets) || [];
      presets.forEach(p => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.label || p.id;
        opt.dataset.target = p.target_table;
        sel.appendChild(opt);
      });
      sel.addEventListener("change", () => {
        const chosen = sel.options[sel.selectedIndex];
        const target = chosen && chosen.dataset.target;
        if (target) {
          const t = $("#import-target");
          if (t) t.value = target;
        }
      });
    } catch (_err) {
      // presets unavailable — manual mapping only
    }
  }

  // Step 1 — upload
  async function onUpload(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const file = form.querySelector("[name='file']").files[0];
    const target = form.querySelector("[name='target']").value;
    const presetId = form.querySelector("[name='preset']").value || "";
    const dateFormat = form.querySelector("[name='date_format']").value || "";
    if (!file) return;
    state.target = target;
    state.presetId = presetId;
    state.dateFormat = dateFormat;

    const body = new FormData();
    body.append("file", file);

    try {
      const res = await fetch("/api/import/upload", { method: "POST", body });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Upload failed (${res.status})`);
      }
      const data = await res.json();
      state.uploadId = data.upload_id;
      state.sheets = data.sheet_names || [];
      state.sheet = data.active_sheet || (state.sheets[0] || null);
      state.columns = data.detected_columns || [];
      state.preview = data.preview || [];

      // Populate sheet selector if multi-sheet
      const sheetField = $("#import-sheet-field");
      const sheetSel = $("#import-sheet");
      if (state.sheets.length > 1 && sheetField && sheetSel) {
        sheetSel.innerHTML = state.sheets.map(s =>
          `<option value="${s}"${s === state.sheet ? " selected" : ""}>${s}</option>`
        ).join("");
        sheetField.hidden = false;
      }

      // Preset flow: skip manual mapping, go straight to dry-run
      if (state.presetId) {
        await runDryRun();
        return;
      }

      // Manual flow: fetch suggested mapping
      await fetchSuggestedMapping();
      renderMapping();
      setStep(2);
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Upload failed", message: err.message });
    }
  }

  async function fetchSuggestedMapping() {
    state.mapping = {};
    try {
      const data = await window.Vaani.api(
        `/api/import/${state.uploadId}/suggest?target_table=${encodeURIComponent(state.target)}`
      );
      const suggestions = (data && data.suggestions) || {};
      // suggestions shape from backend: {source_col: target_field, ...}
      // flip to {target_field: source_col} for UI
      Object.entries(suggestions).forEach(([src, tgt]) => {
        if (tgt) state.mapping[tgt] = src;
      });
    } catch (_err) {
      // no suggestions available
    }
  }

  function renderMapping() {
    const wrap = $("#import-mapping");
    if (!wrap) return;
    const targets = TARGET_SCHEMAS[state.target] || [];
    wrap.innerHTML = "";

    if (!state.columns.length) {
      wrap.innerHTML = '<div class="empty-state" style="padding: var(--sp-4);">No columns detected in the uploaded file. Try a different file.</div>';
      return;
    }

    // Header row
    const head = document.createElement("div");
    head.className = "hstack";
    head.style.cssText = "padding:6px 0; border-bottom:1px solid var(--border-2); font-size:var(--fs-xs); text-transform:uppercase; letter-spacing:0.08em; color:var(--text-3);";
    head.innerHTML = '<div style="width:40%;">Target field</div><div style="width:60%;">Source column</div>';
    wrap.appendChild(head);

    targets.forEach(t => {
      const row = document.createElement("div");
      row.className = "hstack";
      row.style.cssText = "padding:6px 0; border-bottom:1px solid var(--border-1); gap: var(--sp-3);";
      const label = document.createElement("div");
      label.style.cssText = "width: 40%; font-size: var(--fs-sm); color: var(--text-2); font-weight: var(--fw-medium);";
      label.textContent = t;
      const select = document.createElement("select");
      select.className = "select";
      select.style.width = "60%";
      const current = state.mapping[t] || "";
      select.innerHTML = '<option value="">— unmapped —</option>' +
        state.columns.map(c => `<option value="${c}"${current === c ? " selected" : ""}>${c}</option>`).join("");
      select.addEventListener("change", () => { state.mapping[t] = select.value; });
      row.appendChild(label);
      row.appendChild(select);
      wrap.appendChild(row);
    });
    renderPreview();
  }

  function renderPreview() {
    const wrap = $("#import-preview");
    if (!wrap) return;
    if (!state.preview.length) { wrap.innerHTML = '<div class="muted" style="padding: var(--sp-3);">No preview available.</div>'; return; }
    const cols = state.columns;
    const rows = state.preview.slice(0, 10);
    let html = '<table class="table"><thead><tr>' +
      cols.map(c => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
    rows.forEach(r => {
      html += "<tr>" + cols.map(c => `<td>${r[c] === null || r[c] === undefined ? "" : String(r[c])}</td>`).join("") + "</tr>";
    });
    html += "</tbody></table>";
    wrap.innerHTML = html;
  }

  // Flip UI mapping ({target_field: source_col}) to backend shape ({source_col: target_field})
  function mappingForBackend() {
    const out = {};
    Object.entries(state.mapping).forEach(([tgt, src]) => {
      if (src) out[src] = tgt;
    });
    return out;
  }

  // Step 2 → Step 3 dry-run
  async function onDryRun() {
    await runDryRun();
  }

  async function runDryRun() {
    try {
      const body = {
        target_table: state.target,
        mapping: mappingForBackend(),
      };
      if (state.presetId) body.preset_id = state.presetId;
      if (state.sheet) body.sheet_name = state.sheet;
      // User-chosen date format overrides preset default (empty string = auto-detect = omit)
      if (state.dateFormat) body.date_format = state.dateFormat;

      const data = await window.Vaani.api(`/api/import/${state.uploadId}/map`, {
        method: "POST", body,
      });
      state.dryRun = data;
      renderDryRun();
      setStep(3);
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Validation failed", message: err.message });
    }
  }

  function renderDryRun() {
    const wrap = $("#import-dryrun");
    if (!wrap || !state.dryRun) return;
    const d = state.dryRun;
    const valid = d.valid_rows ?? d.valid_count ?? 0;
    const invalid = d.invalid_rows ?? d.invalid_count ?? 0;
    const dups = d.duplicate_rows ?? d.duplicate_count ?? 0;
    const total = d.total_rows ?? d.total_count ?? 0;
    const skipped = d.skipped_rows ?? 0;
    const balanceAdjusts = d.balance_adjust_rows ?? 0;
    const errs = d.errors || [];
    const checksums = d.checksum_report || [];

    wrap.innerHTML = `
      <div class="grid-4" style="margin-bottom: var(--sp-4);">
        <div class="kpi kpi--invest">
          <div class="kpi__label">Valid rows</div>
          <div class="kpi__value">${valid}</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">Invalid rows</div>
          <div class="kpi__value" style="color: var(--num-negative, var(--danger));">${invalid}</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">Duplicates</div>
          <div class="kpi__value" style="color: var(--text-3);">${dups}</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">Total in file</div>
          <div class="kpi__value">${total}</div>
        </div>
      </div>
      ${skipped || balanceAdjusts ? `
        <div class="muted" style="font-size: var(--fs-sm); margin-bottom: var(--sp-3);">
          ${skipped ? `Skipped: ${skipped} rows (Total/summary rows).` : ""}
          ${balanceAdjusts ? ` Balance-adjust rows: ${balanceAdjusts}.` : ""}
        </div>
      ` : ""}
      ${errs.length ? `
        <div class="card" style="margin-top: var(--sp-3);">
          <div class="card__title">Validation issues (first ${Math.min(20, errs.length)})</div>
          <ul style="margin: var(--sp-3) 0 0; padding-left: var(--sp-5); color: var(--text-2); font-size: var(--fs-sm);">
            ${errs.slice(0, 20).map(e => `<li>Row ${e.row_index ?? e.row ?? "?"}: ${(e.errors || [e.message || ""]).join("; ")}</li>`).join("")}
          </ul>
        </div>
      ` : ""}
      ${checksums.length ? `
        <div class="card" style="margin-top: var(--sp-3);">
          <div class="card__title">Daily checksum (file Total vs computed)</div>
          <table class="table" style="margin-top: var(--sp-2); font-size: var(--fs-sm);">
            <thead><tr><th>Day</th><th>Declared</th><th>Computed</th><th>Δ</th><th>Match</th></tr></thead>
            <tbody>
              ${checksums.slice(0, 30).map(c => `
                <tr>
                  <td class="mono">${c.day}</td>
                  <td class="num">${c.declared_total?.toFixed?.(2) ?? "—"}</td>
                  <td class="num">${c.computed_total?.toFixed?.(2) ?? "—"}</td>
                  <td class="num" style="color:${c.match ? "var(--success)" : "var(--danger)"};">${c.delta?.toFixed?.(2) ?? "—"}</td>
                  <td>${c.match ? "✓" : "✗"}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : ""}
    `;
  }

  // Step 4 — commit
  async function onCommit(e) {
    e.preventDefault();
    const strategy = e.currentTarget.querySelector("[name='strategy']").value;
    try {
      const res = await window.Vaani.api(`/api/import/${state.uploadId}/commit`, {
        method: "POST", body: { on_invalid: strategy },
      });
      state.lastCommit = res;
      window.Vaani.toast({
        type: "success", title: "Imported",
        message: `${res.inserted || 0} row${res.inserted === 1 ? "" : "s"} added.`,
      });
      setStep(4);
      const summary = $("#import-summary");
      if (summary) summary.textContent = JSON.stringify(res, null, 2);
      renderDemoPrompt(res);
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Import failed", message: err.message });
    }
  }

  function renderDemoPrompt(commitRes) {
    const host = $("#import-summary");
    if (!host || !commitRes) return;
    if (!commitRes.demo_data_present) return;

    const wrap = document.createElement("div");
    wrap.className = "card";
    wrap.style.marginTop = "var(--sp-5)";
    wrap.innerHTML = `
      <div class="card__title">Clear demo data?</div>
      <p class="muted" style="font-size: var(--fs-sm); margin: var(--sp-3) 0 var(--sp-4);">
        You still have seeded demo rows from first boot. Clear them so your charts and totals reflect only your real imports.
      </p>
      <div class="hstack" style="gap: var(--sp-3);">
        <button type="button" class="btn" data-action="demo-keep">Keep demo data</button>
        <button type="button" class="btn btn--primary" data-action="demo-clear">Clear demo data</button>
      </div>
    `;
    host.after(wrap);

    wrap.querySelector("[data-action='demo-keep']").addEventListener("click", () => wrap.remove());
    wrap.querySelector("[data-action='demo-clear']").addEventListener("click", async () => {
      try {
        const out = await window.Vaani.api("/api/demo-data/purge", { method: "POST", body: {} });
        const total = Object.values(out.removed || {}).reduce((a, b) => a + (b || 0), 0);
        window.Vaani.toast({ type: "success", title: "Demo data cleared", message: `${total} rows removed.` });
        wrap.remove();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Purge failed", message: err.message });
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    loadPresets();

    const uploadForm = $("#import-upload-form");
    if (uploadForm) uploadForm.addEventListener("submit", onUpload);

    const sheetSel = $("#import-sheet");
    if (sheetSel) sheetSel.addEventListener("change", async () => {
      state.sheet = sheetSel.value;
      await fetchSuggestedMapping();
      renderMapping();
    });

    const dryBtn = $("[data-action='import-dryrun']");
    if (dryBtn) dryBtn.addEventListener("click", onDryRun);

    const commitForm = $("#import-commit-form");
    if (commitForm) commitForm.addEventListener("submit", onCommit);

    $$("[data-action='import-back']").forEach(b =>
      b.addEventListener("click", () => setStep(Math.max(1, state.step - 1)))
    );
    $$("[data-action='import-reset']").forEach(b =>
      b.addEventListener("click", () => {
        state.step = 1; state.uploadId = null; state.presetId = ""; state.mapping = {};
        setStep(1);
      })
    );

    setStep(1);
  });
})();

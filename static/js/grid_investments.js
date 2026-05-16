/* =====================================================================
   Vaani — grid_investments.js
   Monthly investments grid. Upsert-by-month semantics against
   /api/investments. Columns are driven by the universal user-column
   registry at /api/tables/investments/columns. Footer row shows per-
   column totals (client-side).
   ===================================================================== */
(function () {
  "use strict";

  const EXCLUDED_FROM_TOTAL = new Set(["total", "month", "import_batch_id"]);

  let hot = null;
  let columnMeta = [];

  async function loadRegistry() {
    try {
      const data = await window.Vaani.api("/api/tables/investments/columns");
      return Array.isArray(data?.columns) ? data.columns : [];
    } catch {
      return [];
    }
  }

  async function loadRows() {
    try {
      const data = await window.Vaani.api("/api/investments");
      return Array.isArray(data) ? data : (data.items || []);
    } catch {
      return [];
    }
  }

  function numericRenderer(instance, td, row, col, prop, value) {
    td.className = "num htNumeric";
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) {
      td.textContent = "—";
      td.style.color = "var(--text-3)";
      return td;
    }
    td.textContent = window.Vaani.fmtNum(Number(value));
    td.style.color = "";
    return td;
  }

  function computeColumnTotals(rows, numericKeys) {
    const totals = {};
    for (const key of numericKeys) totals[key] = 0;
    for (const row of rows) {
      for (const key of numericKeys) {
        const v = Number(row?.[key]);
        if (!Number.isNaN(v)) totals[key] += v;
      }
    }
    return totals;
  }

  function renderFooter(rows, numericKeys) {
    const footer = document.getElementById("investments-footer");
    if (!footer) return;
    const totals = computeColumnTotals(rows, numericKeys);
    const grandTotal = Object.values(totals).reduce((a, b) => a + b, 0);

    // Measure live widths from the master HOT header row, including the
    // row-header gutter. This is the only way to stay aligned under
    // stretchH:"all" + arbitrary user columns.
    const container = document.getElementById("investments-grid");
    const headerRow = container?.querySelector(".ht_master .htCore thead tr");
    const headerCells = headerRow ? Array.from(headerRow.children) : [];
    // headerCells[0] is the row-header (corner) th; the rest are data columns.

    // Build the cell value list aligned to data columns: [Month, ...numericKeys, Total]
    const dataValues = [
      { text: "Total", align: "left", weight: "var(--fw-semi)" },
      ...numericKeys.map(k => ({
        text: window.Vaani.fmtNum(totals[k] || 0),
        align: "right",
        cls: "mono num",
      })),
      {
        text: window.Vaani.fmtNum(grandTotal),
        align: "right",
        weight: "var(--fw-semi)",
        cls: "mono num",
      },
    ];

    // If we couldn't read the header, fall back to a simple aligned row.
    if (headerCells.length < dataValues.length + 1) {
      footer.style.display = "grid";
      footer.style.gap = "0";
      footer.style.padding = "var(--sp-2) 0";
      footer.style.gridTemplateColumns = `repeat(${dataValues.length}, 1fr)`;
      footer.innerHTML = dataValues.map(v =>
        `<div class="${v.cls || ""}" style="padding:0 8px;text-align:${v.align};font-weight:${v.weight || "inherit"}">${v.text}</div>`
      ).join("");
      return;
    }

    const cornerW = headerCells[0].getBoundingClientRect().width;
    const dataWidths = headerCells.slice(1).map(th => th.getBoundingClientRect().width);

    const parts = [];
    parts.push(`<div style="width:${cornerW}px;flex:0 0 ${cornerW}px"></div>`);
    dataValues.forEach((v, i) => {
      const w = dataWidths[i] || 0;
      const style = [
        `width:${w}px`,
        `flex:0 0 ${w}px`,
        "box-sizing:border-box",
        "padding:0 8px",
        "overflow:hidden",
        "text-overflow:ellipsis",
        "white-space:nowrap",
        `text-align:${v.align}`,
        v.weight ? `font-weight:${v.weight}` : "",
      ].filter(Boolean).join(";");
      parts.push(`<div class="${v.cls || ""}" style="${style}">${v.text}</div>`);
    });

    footer.style.display = "flex";
    footer.style.gap = "0";
    footer.style.padding = "var(--sp-2) 0";
    footer.innerHTML = parts.join("");
  }

  function buildColumns(registryCols) {
    // Built-in numeric columns from registry (dtype=number and not excluded)
    const numericMeta = registryCols.filter(
      c => c.dtype === "number" && !EXCLUDED_FROM_TOTAL.has(c.key)
    );
    return {
      numericMeta,
      columns: [
        { data: "month", title: "Month", type: "text", width: 110 },
        ...numericMeta.map(c => ({
          data: c.key,
          title: c.label || c.key,
          type: "numeric",
          numericFormat: { pattern: "0,0.00" },
          className: "num",
          renderer: numericRenderer,
          width: 140,
        })),
        {
          data: "total",
          title: "Total",
          type: "numeric",
          readOnly: true,
          numericFormat: { pattern: "0,0.00" },
          className: "num",
          renderer: numericRenderer,
          width: 140,
        },
      ],
    };
  }

  async function saveRow(row, prop) {
    if (!row?.month) return;
    const body = { [prop]: toNumericOrNull(row[prop]) };
    try {
      await window.Vaani.api(`/api/investments/${row.month}`, {
        method: "PATCH",
        body,
      });
      await refreshTotals();
    } catch (err) {
      // If the month doesn't exist yet, create it via POST (upsert)
      if (String(err.message || "").includes("404")) {
        try {
          const payload = { month: row.month };
          for (const key of columnMeta.map(c => c.key)) {
            if (!EXCLUDED_FROM_TOTAL.has(key)) payload[key] = toNumericOrNull(row[key]);
          }
          await window.Vaani.api("/api/investments", { method: "POST", body: payload });
          await refreshTotals();
          return;
        } catch (err2) {
          window.Vaani.toast({ type: "danger", title: "Save failed", message: err2.message });
          return;
        }
      }
      window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
    }
  }

  function toNumericOrNull(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }

  async function refreshTotals() {
    const rows = await loadRows();
    if (hot) hot.loadData(rows.length ? rows : [{ month: new Date().toISOString().slice(0, 7) }]);
    renderFooter(rows, columnMeta.map(c => c.key).filter(k => !EXCLUDED_FROM_TOTAL.has(k)));
  }

  async function render() {
    const container = document.getElementById("investments-grid");
    if (!container || !window.Handsontable) return;

    const registry = await loadRegistry();
    const { numericMeta, columns } = buildColumns(registry);
    columnMeta = numericMeta;

    const rows = await loadRows();

    if (hot) hot.destroy();
    hot = new Handsontable(container, {
      data: rows.length ? rows : [{ month: new Date().toISOString().slice(0, 7) }],
      columns,
      colHeaders: columns.map(c => c.title),
      rowHeaders: true,
      stretchH: "all",
      height: "auto",
      minSpareRows: 0,
      columnHeaderHeight: 40,
      rowHeights: 40,
      fixedRowsTop: 0,
      renderAllRows: true,
      licenseKey: "non-commercial-and-evaluation",
      afterChange: async (changes, source) => {
        if (!changes || source === "loadData") return;
        for (const [rowIdx, prop, oldVal, newVal] of changes) {
          if (oldVal === newVal) continue;
          if (prop === "total") continue;
          const row = hot.getSourceDataAtRow(rowIdx);
          if (!row?.month) continue;
          await saveRow(row, prop);
        }
      },
    });
    // Force two renders after paint to shake out any layout race where HOT
    // miscalculates the header clone offset and hides row 0.
    requestAnimationFrame(() => {
      try { hot.render(); } catch (_e) {}
      try { hot.scrollViewportTo({ row: 0, col: 0, verticalSnap: "top" }); } catch (_e) {}
      renderFooter(rows, numericMeta.map(c => c.key));
    });
    setTimeout(() => {
      try { hot.render(); } catch (_e) {}
      renderFooter(rows, numericMeta.map(c => c.key));
    }, 120);
    // Re-align footer on viewport resize.
    window.addEventListener("resize", () => renderFooter(rows, numericMeta.map(c => c.key)), { passive: true });
  }

  // -- Add month modal --
  function openAddMonthModal() {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.setAttribute("role", "dialog");
    backdrop.setAttribute("aria-modal", "true");
    backdrop.innerHTML = `
      <div class="modal">
        <div class="modal__head">
          <h3 class="modal__title">Add month</h3>
          <button type="button" class="btn btn--ghost btn--icon" data-action="close" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <form class="vstack" style="gap: var(--sp-3);">
          <div class="field">
            <label class="field__label" for="addmonth-input">Month</label>
            <input class="input mono" id="addmonth-input" name="month" type="month" required
              value="${new Date().toISOString().slice(0, 7)}">
            <div class="field__hint">Starts with zeros; fill values inline in the grid.</div>
          </div>
          <div class="modal__foot">
            <button type="button" class="btn" data-action="close">Cancel</button>
            <button type="submit" class="btn btn--primary">Add month</button>
          </div>
        </form>
      </div>`;
    document.body.appendChild(backdrop);

    const close = () => backdrop.remove();
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    backdrop.querySelectorAll("[data-action='close']").forEach(b => b.addEventListener("click", close));

    const form = backdrop.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const month = form.querySelector("[name='month']").value.trim();
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) {
        window.Vaani.toast({ type: "danger", message: "Invalid month" });
        return;
      }
      try {
        await window.Vaani.api("/api/investments", {
          method: "POST",
          body: { month },
        });
        window.Vaani.toast({ type: "success", message: `Added ${month}` });
        close();
        await render();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Could not add month", message: err.message });
      }
    });
    backdrop.querySelector("[name='month']")?.focus();
  }

  document.addEventListener("DOMContentLoaded", () => {
    render();
    const addColBtn = document.querySelector("[data-action='add-investment-column']");
    if (addColBtn && window.Vaani && typeof window.Vaani.openAddColumnModal === "function") {
      addColBtn.addEventListener("click", () => {
        window.Vaani.openAddColumnModal({
          table: "investments",
          onAdded: () => render(),
        });
      });
    }
    const addMonthBtn = document.querySelector("[data-action='add-investment-month']");
    if (addMonthBtn) addMonthBtn.addEventListener("click", openAddMonthModal);
  });
})();

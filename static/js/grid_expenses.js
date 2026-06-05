/* =====================================================================
   Vaani — grid_expenses.js
   Handsontable grid for /expenses. Chip dropdown editors, inline add,
   POST/PATCH to /api/expenses.

   Storage format for `type_category` is "Type, Category" (comma + space).
   The grid presents it as ONE column — a dropdown listing all 12 combinations
   (3 types × 4 categories) in comma form. Pick one, it's persisted as-is.

   Payment column is a SINGLE fixed dropdown with 5 values:
     paid       — online / upi / card / net banking / gpay / phonepe
     paid_cash  — physical cash
     paid_by    — someone else paid (person_name = who paid for user)
     paid_for   — user paid for someone (backend defaults paid_for_method=online)
     adjusted   — balance transfer (backend defaults adjustment_type=cash_to_online)
   No sub-dropdowns in the grid — advanced fields (method/direction) are set
   via voice or API when needed; defaults work for the common case.
   ===================================================================== */
(function () {
  "use strict";

  const TYPES = ["Need", "Want", "Investment"];
  const CATEGORIES = ["Food & Drinks", "Travel", "Enjoyment", "Miscellaneous"];
  // All 12 combinations in comma form — single-dropdown source for the grid cell.
  const TYPE_CATEGORIES = [];
  TYPES.forEach(t => CATEGORIES.forEach(c => TYPE_CATEGORIES.push(`${t}, ${c}`)));
  const PAYMENTS = ["paid", "paid_cash", "paid_by", "paid_for", "adjusted"];

  // Sentinel shown at the top of the Tag dropdown. Picking it opens the
  // "create a tag" flow instead of storing the literal string.
  const ADD_TAG_OPTION = "➕ Add new tag…";

  // Known custom tags (uniques.tags). Loaded once at init, refreshed after a
  // new tag is created so every cell's dropdown sees it.
  let knownTags = [];

  async function loadTags() {
    try {
      const res = await window.Vaani.api("/api/budgets/tags");
      knownTags = Array.isArray(res.tags) ? res.tags : [];
    } catch (_e) {
      knownTags = [];
    }
    return knownTags;
  }

  function tagDropdownSource() {
    // "(none)" lets the user clear a tag back to null; ADD_TAG_OPTION opens the
    // create flow. The rest are the real tags.
    return ["(none)", ADD_TAG_OPTION, ...knownTags];
  }

  // Prompt for a new tag name + its Need/Want/Investment type, POST it, and
  // return the created tag name (or null if the user cancelled / it failed).
  async function promptCreateTag(prefillName) {
    const name = (prompt("New tag name (e.g. Gym, Netflix, Rent):", prefillName || "") || "").trim();
    if (!name) return null;
    // Closed 3-way choice. prompt() keeps it dependency-free and consistent
    // with the rest of this page's lightweight dialogs.
    const ans = (prompt(
      `Is "${name}" a Need, a Want, or an Investment?\n\nType one: need / want / investment`,
      "need"
    ) || "").trim().toLowerCase();
    const map = { need: "Need", want: "Want", investment: "Investment" };
    const type = map[ans];
    if (!type) {
      window.Vaani.toast({ type: "danger", message: "Tag not created — type must be need, want, or investment." });
      return null;
    }
    try {
      await window.Vaani.api("/api/budgets/tags", {
        method: "POST",
        body: { name, type },
      });
      await loadTags();
      window.Vaani.toast({ type: "success", message: `Tag "${name}" (${type}) created` });
      return name;
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Tag create failed", message: err.message });
      return null;
    }
  }

  function chipClassForTypeCategory(v) {
    if (!v) return "";
    if (v.startsWith("Need"))       return "hot-chip-cell hot-chip-cell--need";
    if (v.startsWith("Want"))       return "hot-chip-cell hot-chip-cell--want";
    if (v.startsWith("Investment")) return "hot-chip-cell hot-chip-cell--investment";
    return "hot-chip-cell";
  }
  function chipClassForTag(v) {
    if (!v) return "";
    return "hot-chip-cell hot-chip-cell--tag";
  }
  function chipClassForPayment(v) {
    if (v === "paid")      return "hot-chip-cell hot-chip-cell--paid";
    if (v === "paid_cash") return "hot-chip-cell hot-chip-cell--cash";
    if (v === "paid_by")   return "hot-chip-cell hot-chip-cell--paid-by";
    if (v === "paid_for")  return "hot-chip-cell hot-chip-cell--paid-for";
    if (v === "adjusted")  return "hot-chip-cell hot-chip-cell--adjusted";
    return "hot-chip-cell";
  }

  function chipRenderer(classResolver) {
    return function (instance, td, row, col, prop, value) {
      td.innerHTML = "";
      td.className = td.className.replace(/\bhot-chip-wrap\b/g, "");
      if (value === null || value === undefined || value === "") {
        td.innerHTML = '<span class="muted" style="font-size:0.78rem;">—</span>';
        return td;
      }
      const span = document.createElement("span");
      span.className = classResolver(value);
      span.textContent = value;
      td.appendChild(span);
      return td;
    };
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

  async function init() {
    const container = document.getElementById("expenses-grid");
    if (!container || !window.Handsontable) return;

    let rows = [];
    try {
      const [data] = await Promise.all([
        window.Vaani.api("/api/expenses"),
        loadTags(),
      ]);
      rows = Array.isArray(data) ? data : (data.items || []);
    } catch (err) {
      rows = [];
    }
    const columns = [
      { data: "date", title: "Date", type: "date", dateFormat: "YYYY-MM-DD", correctFormat: true, width: 110 },
      { data: "expense_name", title: "Expense", type: "text", width: 200 },
      {
        data: "type_category", title: "Type, Category", type: "dropdown",
        source: TYPE_CATEGORIES, strict: true, allowInvalid: false,
        renderer: chipRenderer(chipClassForTypeCategory),
        width: 220,
      },
      {
        // Custom budget tag (expenses.custom_tag). Closed dropdown over known
        // tags + a "create new" sentinel. Lets users tag already-logged
        // expenses so Table C picks them up; new tags are sent to the LLM.
        data: "custom_tag", title: "Tag", type: "dropdown",
        // Source is dynamic — recomputed per-open so freshly created tags show
        // up without rebuilding the grid.
        source(query, process) { process(tagDropdownSource()); },
        strict: false, allowInvalid: true,
        renderer: chipRenderer(chipClassForTag),
        width: 150,
      },
      {
        data: "payment_method", title: "Payment", type: "dropdown",
        source: PAYMENTS, strict: true, allowInvalid: false,
        renderer: chipRenderer(chipClassForPayment),
        width: 130,
      },
      { data: "amount", title: "Amount", type: "numeric", numericFormat: { pattern: "0,0.00" }, className: "num", renderer: numericRenderer, width: 120 },
      { data: "person_name", title: "Person", type: "text", width: 140 },
      { data: "notes", title: "Notes", type: "text", width: 200 },
    ];

    const hot = new Handsontable(container, {
      data: rows,
      columns,
      colHeaders: columns.map(c => c.title),
      rowHeaders: true,
      stretchH: "last",
      height: "auto",
      minSpareRows: 1,
      columnHeaderHeight: 40,   // explicit — prevents layout race that hides row 0
      rowHeights: 40,
      fixedRowsTop: 0,
      contextMenu: ["row_above", "row_below", "---------", "remove_row", "---------", "copy", "cut"],
      licenseKey: "non-commercial-and-evaluation",
      autoWrapRow: true,
      autoWrapCol: true,
      renderAllRows: true,      // render all rows — the expenses list is ≤ a few hundred, cheap
      columnSorting: true,
      afterChange: async (changes, source) => {
        if (!changes || source === "loadData") return;
        for (const [rowIdx, prop, oldVal, newVal] of changes) {
          if (oldVal === newVal) continue;
          const row = hot.getSourceDataAtRow(rowIdx);

          // --- Tag column: resolve sentinels before the generic save path. ---
          if (prop === "custom_tag") {
            if (newVal === "(none)" || newVal === "") {
              // Normalize "clear" to null and persist it.
              if (newVal !== null) hot.setDataAtRowProp(rowIdx, "custom_tag", null);
              if (!row.id) continue; // unsaved blank row — nothing to PATCH yet
              try {
                await window.Vaani.api(`/api/expenses/${row.id}`, {
                  method: "PATCH", body: { custom_tag: null },
                });
                window.Vaani.toast({ type: "success", message: "Tag cleared" });
              } catch (err) {
                window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
              }
              continue;
            }
            if (newVal === ADD_TAG_OPTION) {
              const created = await promptCreateTag("");
              // Revert the sentinel; apply the created tag (or restore old value).
              hot.setDataAtRowProp(rowIdx, "custom_tag", created || oldVal || null);
              if (created && row.id) {
                try {
                  await window.Vaani.api(`/api/expenses/${row.id}`, {
                    method: "PATCH", body: { custom_tag: created },
                  });
                  window.Vaani.toast({ type: "success", message: `Tagged: ${created}` });
                } catch (err) {
                  window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
                }
              }
              continue;
            }
            // A real tag was chosen — fall through to the generic save below.
          }

          // Backend defaults paid_for_method=online and adjustment_type=cash_to_online
          // when those sub-fields are omitted — the grid doesn't surface them.
          const isAdjusted = row.payment_method === "adjusted";
          if (!isAdjusted && (!row.expense_name || !row.amount || !row.type_category || !row.payment_method)) continue;
          if (isAdjusted && !row.amount) continue;

          try {
            if (row.id) {
              await window.Vaani.api(`/api/expenses/${row.id}`, {
                method: "PATCH",
                body: { [prop]: newVal },
              });
            } else {
              // Build a clean POST payload — only the fields ExpenseIn expects.
              // Handsontable rows carry undefined/string junk for untouched cells
              // which pydantic then rejects.
              const payload = {
                date: row.date,
                expense_name: row.expense_name,
                type_category: row.type_category,
                payment_method: row.payment_method,
                amount: Number(String(row.amount).replace(/[₹,\s]/g, "")),
              };
              if (row.person_name) payload.person_name = row.person_name;
              if (row.notes) payload.notes = row.notes;
              // Carry a real tag through on create (skip sentinels / cleared).
              if (row.custom_tag && row.custom_tag !== "(none)" && row.custom_tag !== ADD_TAG_OPTION) {
                payload.custom_tag = row.custom_tag;
              }
              const created = await window.Vaani.api("/api/expenses", { method: "POST", body: payload });
              if (created && created.id) row.id = created.id;
            }
            window.Vaani.toast({ type: "success", message: "Saved" });
          } catch (err) {
            window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
          }
        }
      },
    });

    window.Vaani._expensesGrid = hot;
    // Force two renders after paint to shake out any layout race where HOT
    // miscalculates the header clone offset and hides row 0.
    requestAnimationFrame(() => {
      try { hot.render(); } catch (_e) {}
      try { hot.scrollViewportTo({ row: 0, col: 0, verticalSnap: "top" }); } catch (_e) {}
    });
    setTimeout(() => { try { hot.render(); } catch (_e) {} }, 120);

    // "Teach" hook: Ctrl+Alt click on a row posts the vendor + type_category to uniques.
    hot.addHook("afterOnCellMouseDown", async (event, coords) => {
      if (!event || !event.ctrlKey || !event.altKey) return;
      const row = hot.getSourceDataAtRow(coords.row);
      if (!row || !row.expense_name) return;
      try {
        await window.Vaani.api("/api/uniques/teach", {
          method: "POST",
          body: {
            surface: row.expense_name,
            vendor: row.expense_name,
            type_category: row.type_category || null,
          },
        });
        window.Vaani.toast({ type: "success", message: `Taught: ${row.expense_name}` });
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Teach failed", message: err.message });
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    init();
    const addBtn = document.querySelector("[data-action='add-expenses-column']");
    if (addBtn && window.Vaani && typeof window.Vaani.openAddColumnModal === "function") {
      addBtn.addEventListener("click", () => {
        window.Vaani.openAddColumnModal({
          table: "expenses",
          onAdded: () => { if (typeof init === "function") init(); },
        });
      });
    }
  });
})();

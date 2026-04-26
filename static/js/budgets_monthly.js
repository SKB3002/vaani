/* Budget Table C viewer — month selector, warnings, row coloring. */
(function () {
  "use strict";
  const { api, toast, fmtNum } = window.FinEye;

  const monthInput = document.getElementById("bc-month");
  const rowsEl = document.getElementById("bc-rows");
  const emptyEl = document.getElementById("bc-empty");
  const recomputeBtn = document.getElementById("bc-recompute");
  const warnWrap = document.getElementById("bc-warnings");
  const warnList = document.getElementById("bc-warnings-list");

  function currentMonth() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }

  function fmt(n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    try { return fmtNum ? fmtNum(Number(n)) : Number(n).toFixed(2); } catch { return String(n); }
  }

  function render(rows) {
    rowsEl.innerHTML = "";
    if (!rows || rows.length === 0) {
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    const warnings = [];
    for (const r of rows) {
      const over = Number(r.actual) > Number(r.budget) + Number(r.budget) * 0; // compare to budget_effective approximated as budget+carry
      const heavyEmerg = Number(r.to_emergency) > 0;
      const tr = document.createElement("tr");
      if (over) tr.style.borderLeft = "3px solid var(--color-danger, #c0392b)";
      if (heavyEmerg) tr.style.boxShadow = "inset 3px 0 0 var(--color-gold, #D4AB67)";
      const notesText = r.notes || "";
      if (notesText.includes("overflow_lost")) {
        warnings.push(`${r.category}: ${notesText}`);
      }
      tr.innerHTML = `
        <td>${escapeHtml(r.category)}</td>
        <td class="num mono">${fmt(r.budget)}</td>
        <td class="num mono">${fmt(r.actual)}</td>
        <td class="num mono">${fmt(r.remaining)}</td>
        <td class="num mono">${fmt(r.carry_buffer)}</td>
        <td class="num mono">${fmt(r.overflow)}</td>
        <td class="num mono">${fmt(r.to_medical)}</td>
        <td class="num mono">${fmt(r.to_emergency)}</td>
        <td class="num mono">${fmt(r.med_balance)}</td>
        <td class="num mono">${fmt(r.emerg_balance)}</td>
        <td class="muted" style="font-size: var(--fs-xs);">${escapeHtml(notesText)}</td>
      `;
      rowsEl.appendChild(tr);
    }
    if (warnings.length) {
      warnWrap.hidden = false;
      warnList.innerHTML = warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("");
    } else {
      warnWrap.hidden = true;
    }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  async function load() {
    const month = monthInput.value;
    try {
      const res = await api(`/api/budgets/table-c?month=${encodeURIComponent(month)}`);
      render(res.rows || []);
    } catch (e) {
      toast({ type: "danger", title: "Load failed", message: e.message });
    }
  }

  async function recompute() {
    recomputeBtn.disabled = true;
    recomputeBtn.textContent = "Recomputing…";
    try {
      const res = await api("/api/budgets/recompute", { method: "POST" });
      toast({ type: "success", message: `Recomputed ${res.months_computed} months` });
      await load();
    } catch (e) {
      toast({ type: "danger", title: "Recompute failed", message: e.message });
    } finally {
      recomputeBtn.disabled = false;
      recomputeBtn.textContent = "Recompute";
    }
  }

  monthInput.value = currentMonth();
  monthInput.addEventListener("change", load);
  recomputeBtn.addEventListener("click", recompute);
  load();
})();

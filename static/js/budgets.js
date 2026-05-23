/* Budget rules + caps editor. Hydrates the server-rendered page with live edits. */
(function () {
  "use strict";
  const { api, toast } = window.Vaani;

  function ensureRulesTable() {
    // If the server rendered the empty-state (no rules at first paint) the
    // table doesn't exist yet. Build a fresh table inside the rules card so
    // the JS refresh path always has a tbody to fill.
    let tbody = document.querySelector(".card table.table tbody");
    if (tbody) return tbody;
    const card = document.querySelector(".card");
    if (!card) return null;
    // Strip any existing empty-state node.
    const empty = card.querySelector(".empty-state, [class*='empty']");
    if (empty) empty.remove();
    const table = document.createElement("table");
    table.className = "table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>Category</th>
          <th class="num">Monthly budget</th>
          <th class="num">Carry cap</th>
          <th class="num">Priority</th>
          <th></th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    card.appendChild(table);
    return table.querySelector("tbody");
  }

  async function refreshRules() {
    try {
      const rules = await api("/api/budgets/rules");
      const tbody = ensureRulesTable();
      if (!tbody) return;
      tbody.innerHTML = "";
      for (const r of rules) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escapeHtml(r.category)}</td>
          <td class="num mono">${Number(r.monthly_budget || 0).toFixed(2)}</td>
          <td class="num mono">${Number(r.carry_cap || 0).toFixed(2)}</td>
          <td class="num mono">${r.priority ?? 100}</td>
          <td class="right" style="display:flex;gap:var(--sp-2);justify-content:flex-end;">
            <button class="btn btn--primary btn--sm" data-set-budget="${escapeHtml(r.category)}">Set budget</button>
            <button class="btn btn--ghost btn--sm" data-edit="${escapeHtml(r.category)}">Edit all</button>
            <button class="btn btn--ghost btn--sm" data-del="${escapeHtml(r.category)}">Delete</button>
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-set-budget]").forEach((btn) => {
        btn.addEventListener("click", () => setBudget(btn.dataset.setBudget));
      });
      tbody.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", () => deleteRule(btn.dataset.del));
      });
      tbody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => editRule(btn.dataset.edit));
      });
    } catch (e) {
      toast({ type: "danger", title: "Refresh failed", message: e.message });
    }
  }

  async function setBudget(category) {
    // Single-prompt shortcut: just the monthly budget. Carry cap and
    // priority stay untouched. This is the "set my Medical budget" path.
    const rules = await api("/api/budgets/rules");
    const existing = rules.find((r) => r.category === category);
    if (!existing) return;
    const cur = Number(existing.monthly_budget || 0);
    const mb = prompt(`Monthly budget for "${category}":\n(currently ₹${cur.toFixed(2)})`, cur);
    if (mb === null) return;
    const amount = Number(mb);
    if (!Number.isFinite(amount) || amount < 0) {
      toast({ type: "danger", message: "Enter a non-negative number" });
      return;
    }
    try {
      await api(`/api/budgets/rules/${encodeURIComponent(category)}`, {
        method: "PATCH",
        body: { monthly_budget: amount },
      });
      toast({ type: "success", message: `Budget for ${category} set to ₹${amount.toFixed(2)}` });
      refreshRules();
    } catch (e) {
      toast({ type: "danger", title: "Save failed", message: e.message });
    }
  }

  async function deleteRule(category) {
    if (!confirm(`Delete rule "${category}"?`)) return;
    try {
      await api(`/api/budgets/rules/${encodeURIComponent(category)}`, { method: "DELETE" });
      toast({ type: "success", message: "Rule deleted" });
      refreshRules();
    } catch (e) {
      toast({ type: "danger", title: "Delete failed", message: e.message });
    }
  }

  async function editRule(category) {
    const rules = await api("/api/budgets/rules");
    const existing = rules.find((r) => r.category === category);
    if (!existing) return;
    const mb = prompt(`Monthly budget for "${category}":`, existing.monthly_budget ?? 0);
    if (mb === null) return;
    const cc = prompt(`Carry cap for "${category}":`, existing.carry_cap ?? 0);
    if (cc === null) return;
    const pr = prompt(`Priority for "${category}":`, existing.priority ?? 100);
    if (pr === null) return;
    try {
      await api(`/api/budgets/rules/${encodeURIComponent(category)}`, {
        method: "PATCH",
        body: {
          monthly_budget: Number(mb),
          carry_cap: Number(cc),
          priority: Number(pr),
        },
      });
      toast({ type: "success", message: "Rule updated" });
      refreshRules();
    } catch (e) {
      toast({ type: "danger", title: "Update failed", message: e.message });
    }
  }

  async function addRule() {
    const category = prompt("Category (e.g., Food & Drinks or 'Need, Travel' or electricity):");
    if (!category) return;
    const mb = prompt("Monthly budget:");
    const cc = prompt("Carry cap:");
    const pr = prompt("Priority (lower = earlier, default 100):", "100");
    try {
      await api("/api/budgets/rules", {
        method: "POST",
        body: {
          category: category.trim(),
          monthly_budget: Number(mb) || 0,
          carry_cap: Number(cc) || 0,
          priority: Number(pr) || 100,
        },
      });
      toast({ type: "success", message: "Rule saved" });
      refreshRules();
    } catch (e) {
      toast({ type: "danger", title: "Save failed", message: e.message });
    }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const capsForm = document.getElementById("caps-form");
  if (capsForm) {
    capsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = {};
      new FormData(capsForm).forEach((v, k) => (payload[k] = Number(v)));
      const btn = capsForm.querySelector("[type=submit]");
      if (btn) btn.disabled = true;
      try {
        await api("/api/budgets/caps", { method: "PATCH", body: payload });
        toast({ type: "success", message: "Caps saved" });
      } catch (err) {
        toast({ type: "danger", title: "Save failed", message: err.message });
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  // Add rule button (injected if not present in template)
  const headCard = document.querySelector(".card .card__head");
  if (headCard && !document.getElementById("add-rule-btn")) {
    const btn = document.createElement("button");
    btn.id = "add-rule-btn";
    btn.className = "btn btn--primary btn--sm";
    btn.textContent = "+ Add rule";
    btn.style.marginLeft = "auto";
    btn.addEventListener("click", addRule);
    headCard.appendChild(btn);
  }

  // Recompute button
  if (!document.getElementById("recompute-btn")) {
    const btn = document.createElement("button");
    btn.id = "recompute-btn";
    btn.className = "btn btn--ghost btn--sm";
    btn.textContent = "Recompute";
    btn.style.marginLeft = "var(--sp-2)";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await api("/api/budgets/recompute", { method: "POST" });
        toast({ type: "success", message: `Recomputed ${res.months_computed} months` });
      } catch (e) {
        toast({ type: "danger", title: "Recompute failed", message: e.message });
      } finally {
        btn.disabled = false;
      }
    });
    if (headCard) headCard.appendChild(btn);
  }

  refreshRules();
})();

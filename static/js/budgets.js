/* Budget rules + caps editor. Hydrates the server-rendered page with live edits. */
(function () {
  "use strict";
  const { api, toast } = window.Vaani;

  async function refreshRules() {
    try {
      const rules = await api("/api/budgets/rules");
      // Simple re-render: reload page to pick up server-rendered rows.
      // For an interactive experience, we swap in-place.
      const tbody = document.querySelector(".card table.table tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      for (const r of rules) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escapeHtml(r.category)}</td>
          <td class="num mono">${Number(r.monthly_budget || 0).toFixed(2)}</td>
          <td class="num mono">${Number(r.carry_cap || 0).toFixed(2)}</td>
          <td class="num mono">${r.priority ?? 100}</td>
          <td class="right">
            <button class="btn btn--ghost btn--sm" data-del="${escapeHtml(r.category)}">Delete</button>
          </td>
        `;
        tbody.appendChild(tr);
      }
      tbody.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", () => deleteRule(btn.dataset.del));
      });
    } catch (e) {
      toast({ type: "danger", title: "Refresh failed", message: e.message });
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

  // Caps form: switch PUT → PATCH (router uses PATCH).
  const capsForm = document.getElementById("caps-form");
  if (capsForm) {
    capsForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = {};
      new FormData(capsForm).forEach((v, k) => (payload[k] = Number(v)));
      try {
        await api("/api/budgets/caps", { method: "PATCH", body: payload });
        toast({ type: "success", message: "Caps saved" });
      } catch (err) {
        toast({ type: "danger", title: "Save failed", message: err.message });
      }
    }, true);
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

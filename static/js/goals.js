/* Goals A/B interactive view — cards w/ progress bars, add/contribute modals. */
(function () {
  "use strict";
  const { api, toast } = window.Vaani;

  const mode = document.body.dataset.goalsMode || detectMode();

  function detectMode() {
    const p = window.location.pathname;
    if (p.includes("/sources")) return "sources";
    if (p.includes("/overview")) return "overview";
    return null;
  }

  if (!mode) return;

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function card(g) {
    const pct = Math.min(100, Number(g.pct_complete || 0));
    const pctBar = pct.toFixed(1);
    const statusClass = {
      achieved: "success",
      nearing_goal: "info",
      in_progress: "info",
      just_started: "warn",
    }[g.status] || "info";
    const monthsLeft = g.months_left == null ? "∞" : g.months_left;
    const contribBtn = mode === "sources"
      ? `<button class="btn btn--ghost btn--sm" data-contribute="${escapeHtml(g.goal_id)}">Contribute</button>`
      : "";
    const sourceBits = mode === "sources"
      ? `<div class="muted" style="font-size: var(--fs-xs);">Manual: ${Number(g.manual_saved || 0).toFixed(2)} · Auto: ${Number(g.auto_added || 0).toFixed(2)}</div>`
      : "";
    const currentVal = mode === "sources"
      ? Number(g.total_saved || 0)
      : Number(g.current_amount || 0);
    return `
      <div class="kpi card" data-goal="${escapeHtml(g.goal_id)}" style="padding: var(--sp-4);">
        <div class="hstack" style="justify-content: space-between;">
          <h3 style="font-family: var(--ff-serif, serif); margin: 0;">${escapeHtml(g.goal_name)}</h3>
          <span class="badge badge--${statusClass}">${escapeHtml(g.status || "—")}</span>
        </div>
        <div class="progress" style="height: 8px; background: var(--color-bg-mute); border-radius: 4px; margin: var(--sp-3) 0; overflow: hidden;">
          <div style="height: 100%; width: ${pctBar}%; background: var(--color-gold, #D4AB67);"></div>
        </div>
        <div class="mono" style="font-size: var(--fs-sm);">${currentVal.toFixed(2)} / ${Number(g.target_amount || 0).toFixed(2)} (${pctBar}%)</div>
        <div class="muted" style="font-size: var(--fs-xs); margin-top: var(--sp-2);">
          Monthly: ${Number(g.monthly_contribution || 0).toFixed(2)} · Months left: ${monthsLeft}
        </div>
        ${sourceBits}
        <div class="hstack" style="gap: var(--sp-2); margin-top: var(--sp-3);">
          ${contribBtn}
          <button class="btn btn--ghost btn--sm" data-del="${escapeHtml(g.goal_id)}">Delete</button>
        </div>
      </div>
    `;
  }

  async function load() {
    const endpoint = mode === "overview" ? "/api/goals/overview" : "/api/goals/sources";
    try {
      const goals = await api(endpoint);
      const host = document.getElementById("goals-grid") || createHost();
      if (!goals.length) {
        host.innerHTML = `<div class="muted">No goals yet. Click "+ Add Goal" to start.</div>`;
        return;
      }
      host.innerHTML = goals.map(card).join("");
      host.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", () => deleteGoal(btn.dataset.del));
      });
      host.querySelectorAll("[data-contribute]").forEach((btn) => {
        btn.addEventListener("click", () => contribute(btn.dataset.contribute));
      });
    } catch (e) {
      toast({ type: "danger", title: "Load failed", message: e.message });
    }
  }

  function createHost() {
    const host = document.createElement("div");
    host.id = "goals-grid";
    host.className = "grid-1";
    host.style.gridTemplateColumns = "repeat(auto-fill, minmax(280px, 1fr))";
    host.style.gap = "var(--sp-4)";
    const main = document.querySelector(".main") || document.body;
    const existingCard = main.querySelector(".card");
    if (existingCard) existingCard.replaceWith(host);
    else main.appendChild(host);
    return host;
  }

  async function deleteGoal(goalId) {
    if (!confirm("Delete goal?")) return;
    const endpoint = mode === "overview"
      ? `/api/goals/overview/${encodeURIComponent(goalId)}`
      : `/api/goals/sources/${encodeURIComponent(goalId)}`;
    try {
      await api(endpoint, { method: "DELETE" });
      toast({ type: "success", message: "Deleted" });
      load();
    } catch (e) {
      toast({ type: "danger", title: "Delete failed", message: e.message });
    }
  }

  async function contribute(goalId) {
    const amount = prompt("Amount to contribute:");
    if (!amount) return;
    const kind = prompt("Kind: manual or auto?", "manual");
    if (kind !== "manual" && kind !== "auto") {
      toast({ type: "danger", message: "Kind must be manual or auto" });
      return;
    }
    const sync = confirm("Sync to overview (Goals A) if linked by name?");
    try {
      await api(
        `/api/goals/sources/${encodeURIComponent(goalId)}/contribute?sync_to_overview=${sync}`,
        { method: "POST", body: { amount: Number(amount), kind } }
      );
      toast({ type: "success", message: "Contribution added" });
      load();
    } catch (e) {
      toast({ type: "danger", title: "Failed", message: e.message });
    }
  }

  async function addGoal() {
    const goal_name = prompt("Goal name:");
    if (!goal_name) return;
    const target = Number(prompt("Target amount:", "0")) || 0;
    const monthly = Number(prompt("Monthly contribution:", "0")) || 0;
    const endpoint = mode === "overview" ? "/api/goals/overview" : "/api/goals/sources";
    const body = mode === "overview"
      ? { goal_name, target_amount: target, current_amount: 0, monthly_contribution: monthly }
      : { goal_name, target_amount: target, manual_saved: 0, auto_added: 0, monthly_contribution: monthly };
    try {
      await api(endpoint, { method: "POST", body });
      toast({ type: "success", message: "Goal added" });
      load();
    } catch (e) {
      toast({ type: "danger", title: "Add failed", message: e.message });
    }
  }

  // Inject add button once
  const head = document.querySelector(".page-head") || document.querySelector("h1")?.parentElement;
  if (head && !document.getElementById("add-goal-btn")) {
    const btn = document.createElement("button");
    btn.id = "add-goal-btn";
    btn.className = "btn btn--primary";
    btn.textContent = "+ Add Goal";
    btn.addEventListener("click", addGoal);
    head.appendChild(btn);
  }

  load();
})();

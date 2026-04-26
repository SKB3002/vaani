/* =====================================================================
   Vaani — grid_wishlist.js
   Card-grid UI for /wishlist. CRUD against /api/wishlist.
   ===================================================================== */
(function () {
  "use strict";

  const state = {
    status: "active",
    items: [],
  };

  function priorityBadge(p) {
    if (!p) return "";
    const label = p === "high" ? "High" : p === "med" ? "Medium" : "Low";
    const cls = p === "high" ? "badge--danger" : p === "low" ? "badge--info" : "badge";
    return `<span class="badge ${cls}">${label}</span>`;
  }

  function pctFrom(w) {
    const target = Number(w.target_amount) || 0;
    const saved = Number(w.saved_so_far) || 0;
    if (target <= 0) return 0;
    return Math.max(0, Math.min(100, (saved / target) * 100));
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function cardHtml(w) {
    const pct = pctFrom(w);
    const saved = window.Vaani.fmtNum(Number(w.saved_so_far) || 0);
    const target = window.Vaani.fmtNum(Number(w.target_amount) || 0);
    const aiBadge = w.source === "ai" ? `<span class="badge badge--info">AI-added</span>` : "";
    const statusBadge = `<span class="badge badge--${w.status === 'achieved' ? 'success' : w.status === 'abandoned' ? 'muted' : 'info'}">${w.status || 'active'}</span>`;
    const linkHtml = w.link
      ? `<a class="muted" style="font-size: var(--fs-xs);" href="${escapeHtml(w.link)}" target="_blank" rel="noopener">link</a>`
      : "";
    const notesHtml = w.notes
      ? `<div class="muted" style="font-size: var(--fs-xs); margin-top: var(--sp-1);">${escapeHtml(w.notes)}</div>`
      : "";

    return `
      <div class="card card--interactive" data-wish-id="${escapeHtml(w.id)}">
        <div class="card__head">
          <div>
            <h3 class="card__title" style="font-family: var(--font-serif, serif); font-size: var(--fs-md);">${escapeHtml(w.item)}</h3>
            <div class="hstack" style="gap: var(--sp-1); margin-top: var(--sp-1);">
              ${aiBadge}
              ${priorityBadge(w.priority)}
              ${statusBadge}
            </div>
          </div>
          ${linkHtml}
        </div>

        <div style="display: flex; justify-content: space-between; align-items: baseline; margin: var(--sp-2) 0;">
          <div class="mono" style="font-size: var(--fs-lg); font-weight: var(--fw-semi);">${saved}</div>
          <div class="muted mono">of ${target}</div>
        </div>

        <div style="height: 6px; background: var(--surface-2); border-radius: var(--r-pill); overflow: hidden;">
          <div style="height: 100%; width: ${pct.toFixed(1)}%; background: var(--gold, var(--accent)); transition: width var(--dur-4) var(--ease-out);"></div>
        </div>
        <div class="muted" style="font-size: var(--fs-xs); margin-top: var(--sp-2);">${pct.toFixed(1)}% complete</div>
        ${notesHtml}

        <div class="hstack" style="gap: var(--sp-1); margin-top: var(--sp-3); flex-wrap: wrap;">
          <button type="button" class="btn btn--primary" data-action="contribute">Contribute</button>
          <button type="button" class="btn" data-action="edit">Edit</button>
          ${w.status !== 'achieved' ? `<button type="button" class="btn" data-action="mark-achieved">Mark achieved</button>` : ""}
          ${w.status !== 'abandoned' ? `<button type="button" class="btn btn--ghost" data-action="abandon">Abandon</button>` : ""}
        </div>
      </div>`;
  }

  async function loadAndRender() {
    try {
      const data = await window.Vaani.api(`/api/wishlist?status=${encodeURIComponent(state.status)}`);
      state.items = Array.isArray(data) ? data : (data.items || []);
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Load failed", message: err.message });
      state.items = [];
    }
    const container = document.getElementById("wishlist-grid");
    if (!container) return;
    if (!state.items.length) {
      container.innerHTML = `
        <div class="card" style="text-align: center; padding: var(--sp-6);">
          <div class="muted">No ${state.status === 'all' ? '' : state.status} wishlist items yet.</div>
        </div>`;
      return;
    }
    container.innerHTML = state.items.map(cardHtml).join("");
    container.querySelectorAll("[data-wish-id]").forEach(attachCardHandlers);
  }

  function attachCardHandlers(card) {
    const id = card.getAttribute("data-wish-id");
    const wish = state.items.find(w => w.id === id);
    if (!wish) return;
    card.querySelector("[data-action='contribute']")?.addEventListener("click", () => openContributeModal(wish));
    card.querySelector("[data-action='edit']")?.addEventListener("click", () => openEditModal(wish));
    card.querySelector("[data-action='mark-achieved']")?.addEventListener("click", () => patchWish(id, { status: "achieved" }));
    card.querySelector("[data-action='abandon']")?.addEventListener("click", () => deleteWish(id));
  }

  async function patchWish(id, body) {
    try {
      await window.Vaani.api(`/api/wishlist/${id}`, { method: "PATCH", body });
      window.Vaani.toast({ type: "success", message: "Updated" });
      await loadAndRender();
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Update failed", message: err.message });
    }
  }

  async function deleteWish(id) {
    if (!window.confirm("Abandon this wish?")) return;
    try {
      await window.Vaani.api(`/api/wishlist/${id}`, { method: "DELETE" });
      window.Vaani.toast({ type: "success", message: "Abandoned" });
      await loadAndRender();
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Delete failed", message: err.message });
    }
  }

  // ---- modals ----

  function buildModal(bodyHtml, titleText) {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.setAttribute("role", "dialog");
    backdrop.setAttribute("aria-modal", "true");
    backdrop.innerHTML = `
      <div class="modal">
        <div class="modal__head">
          <h3 class="modal__title">${escapeHtml(titleText)}</h3>
          <button type="button" class="btn btn--ghost btn--icon" data-action="close" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        ${bodyHtml}
      </div>`;
    document.body.appendChild(backdrop);
    const close = () => backdrop.remove();
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    backdrop.querySelectorAll("[data-action='close']").forEach(b => b.addEventListener("click", close));
    return { backdrop, close };
  }

  function openAddModal() {
    const body = `
      <form class="vstack" style="gap: var(--sp-3);">
        <div class="field">
          <label class="field__label" for="wish-item">Item</label>
          <input class="input" id="wish-item" name="item" placeholder="e.g. New bike" required maxlength="200">
        </div>
        <div class="field">
          <label class="field__label" for="wish-target">Target amount</label>
          <input class="input mono" id="wish-target" name="target_amount" type="number" step="1" min="1" placeholder="e.g. 50000" required>
        </div>
        <div class="field">
          <label class="field__label" for="wish-priority">Priority</label>
          <select class="input" id="wish-priority" name="priority">
            <option value="">—</option>
            <option value="high">High</option>
            <option value="med">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
        <div class="field">
          <label class="field__label" for="wish-link">Link <span class="muted">(optional)</span></label>
          <input class="input" id="wish-link" name="link" type="url" placeholder="https://…">
        </div>
        <div class="field">
          <label class="field__label" for="wish-notes">Notes <span class="muted">(optional)</span></label>
          <textarea class="input" id="wish-notes" name="notes" rows="2"></textarea>
        </div>
        <div class="modal__foot">
          <button type="button" class="btn" data-action="close">Cancel</button>
          <button type="submit" class="btn btn--primary">Add wish</button>
        </div>
      </form>`;
    const { backdrop, close } = buildModal(body, "Add wish");
    const form = backdrop.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = new FormData(form);
      const payload = {
        item: String(data.get("item") || "").trim(),
        target_amount: Number(data.get("target_amount")),
        priority: data.get("priority") || null,
        link: (data.get("link") || null) || null,
        notes: (data.get("notes") || null) || null,
      };
      if (!payload.item || !(payload.target_amount > 0)) return;
      try {
        await window.Vaani.api("/api/wishlist", { method: "POST", body: payload });
        window.Vaani.toast({ type: "success", message: "Added" });
        close();
        await loadAndRender();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Add failed", message: err.message });
      }
    });
    form.querySelector("[name='item']")?.focus();
  }

  function openEditModal(wish) {
    const body = `
      <form class="vstack" style="gap: var(--sp-3);">
        <div class="field">
          <label class="field__label" for="edit-item">Item</label>
          <input class="input" id="edit-item" name="item" value="${escapeHtml(wish.item)}" required>
        </div>
        <div class="field">
          <label class="field__label" for="edit-target">Target amount</label>
          <input class="input mono" id="edit-target" name="target_amount" type="number" step="1" min="1" value="${wish.target_amount}" required>
        </div>
        <div class="field">
          <label class="field__label" for="edit-priority">Priority</label>
          <select class="input" id="edit-priority" name="priority">
            <option value=""${!wish.priority ? ' selected' : ''}>—</option>
            <option value="high"${wish.priority === 'high' ? ' selected' : ''}>High</option>
            <option value="med"${wish.priority === 'med' ? ' selected' : ''}>Medium</option>
            <option value="low"${wish.priority === 'low' ? ' selected' : ''}>Low</option>
          </select>
        </div>
        <div class="field">
          <label class="field__label" for="edit-link">Link</label>
          <input class="input" id="edit-link" name="link" type="url" value="${escapeHtml(wish.link || '')}">
        </div>
        <div class="field">
          <label class="field__label" for="edit-notes">Notes</label>
          <textarea class="input" id="edit-notes" name="notes" rows="2">${escapeHtml(wish.notes || '')}</textarea>
        </div>
        <div class="modal__foot">
          <button type="button" class="btn" data-action="close">Cancel</button>
          <button type="submit" class="btn btn--primary">Save</button>
        </div>
      </form>`;
    const { backdrop, close } = buildModal(body, "Edit wish");
    const form = backdrop.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = new FormData(form);
      const payload = {
        item: String(data.get("item") || "").trim(),
        target_amount: Number(data.get("target_amount")),
        priority: data.get("priority") || null,
        link: (data.get("link") || null) || null,
        notes: (data.get("notes") || null) || null,
      };
      try {
        await window.Vaani.api(`/api/wishlist/${wish.id}`, { method: "PATCH", body: payload });
        window.Vaani.toast({ type: "success", message: "Saved" });
        close();
        await loadAndRender();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
      }
    });
  }

  function openContributeModal(wish) {
    const body = `
      <form class="vstack" style="gap: var(--sp-3);">
        <div class="muted" style="font-size: var(--fs-sm);">
          Saving toward <strong>${escapeHtml(wish.item)}</strong>
        </div>
        <div class="field">
          <label class="field__label" for="contrib-amount">Amount</label>
          <input class="input mono" id="contrib-amount" name="amount" type="number" step="1" min="1" required>
        </div>
        <div class="field">
          <label class="field__label">Source</label>
          <div class="hstack" style="gap: var(--sp-2);">
            <label><input type="radio" name="source" value="expense" checked> From expense ledger</label>
            <label><input type="radio" name="source" value="manual"> Manual tick-up</label>
          </div>
          <div class="field__hint">"Expense ledger" also writes an expense row so the Need/Want/Investment pie stays accurate.</div>
        </div>
        <div class="modal__foot">
          <button type="button" class="btn" data-action="close">Cancel</button>
          <button type="submit" class="btn btn--primary">Contribute</button>
        </div>
      </form>`;
    const { backdrop, close } = buildModal(body, "Contribute");
    const form = backdrop.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = new FormData(form);
      const payload = {
        amount: Number(data.get("amount")),
        source: String(data.get("source") || "manual"),
      };
      if (!(payload.amount > 0)) return;
      try {
        await window.Vaani.api(`/api/wishlist/${wish.id}/contribute`, {
          method: "POST",
          body: payload,
        });
        window.Vaani.toast({ type: "success", message: "Contribution saved" });
        close();
        await loadAndRender();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Contribute failed", message: err.message });
      }
    });
    form.querySelector("[name='amount']")?.focus();
  }

  function bindFilters() {
    document.querySelectorAll("[data-wishlist-filter]").forEach(btn => {
      btn.addEventListener("click", async () => {
        state.status = btn.getAttribute("data-wishlist-filter") || "active";
        document.querySelectorAll("[data-wishlist-filter]").forEach(b => {
          b.classList.toggle("btn--primary", b === btn);
        });
        await loadAndRender();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindFilters();
    const addBtn = document.querySelector("[data-action='open-wishlist-add']");
    if (addBtn) addBtn.addEventListener("click", openAddModal);

    const colBtn = document.querySelector("[data-action='add-wishlist-column']");
    if (colBtn && window.Vaani?.openAddColumnModal) {
      colBtn.addEventListener("click", () => {
        window.Vaani.openAddColumnModal({
          table: "wishlist",
          onAdded: () => loadAndRender(),
        });
      });
    }
    loadAndRender();
  });
})();

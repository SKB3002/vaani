/* =====================================================================
   Vaani — add_column_modal.js
   Reusable "+ Add Column" modal usable from any grid that wants to let
   the user extend a CSV with a new user-defined column.
   ===================================================================== */
(function () {
  "use strict";

  /**
   * Open the add-column modal for a given table.
   * @param {Object} opts
   * @param {string} opts.table       Target table key (e.g. "expenses").
   * @param {Function} [opts.onAdded] Optional callback invoked after a successful POST.
   */
  function openAddColumnModal(opts) {
    const { table, onAdded } = opts || {};
    if (!table) return;

    const tpl = document.getElementById("tpl-add-column-modal");
    if (!tpl) {
      // Fallback: build a minimal modal in-place when the page template wasn't included.
      _buildFallbackModal(table, onAdded);
      return;
    }
    const frag = tpl.content.cloneNode(true);
    document.body.appendChild(frag);

    const backdrop = document.querySelector(".modal-backdrop[data-modal='add-column']");
    if (!backdrop) return;
    const form = backdrop.querySelector("form");
    const close = () => backdrop.remove();
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    backdrop.querySelectorAll("[data-action='close']").forEach(b => b.addEventListener("click", close));

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const label = (form.querySelector("[name='label']")?.value || "").trim();
      const key = ((form.querySelector("[name='key']")?.value || "").trim() ||
                    label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""));
      const dtypeEl = form.querySelector("[name='dtype']");
      const dtype = dtypeEl ? dtypeEl.value : "string";
      const defaultEl = form.querySelector("[name='default']");
      const defaultVal = defaultEl && defaultEl.value !== "" ? defaultEl.value : null;
      if (!label || !key) return;
      try {
        await window.Vaani.api(`/api/tables/${table}/columns`, {
          method: "POST",
          body: { key, label, dtype, default: defaultVal },
        });
        window.Vaani.toast({ type: "success", message: `Added column "${label}"` });
        close();
        if (typeof onAdded === "function") await onAdded();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Could not add column", message: err.message });
      }
    });
    const labelEl = form.querySelector("[name='label']");
    if (labelEl) labelEl.focus();
  }

  function _buildFallbackModal(table, onAdded) {
    const label = window.prompt("New column label?");
    if (!label) return;
    const key = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    const dtype = window.prompt("dtype (string / number / boolean / date)", "string") || "string";
    window.Vaani.api(`/api/tables/${table}/columns`, {
      method: "POST",
      body: { key, label, dtype },
    }).then(() => {
      window.Vaani.toast({ type: "success", message: `Added column "${label}"` });
      if (typeof onAdded === "function") onAdded();
    }).catch(err => {
      window.Vaani.toast({ type: "danger", title: "Could not add column", message: err.message });
    });
  }

  window.Vaani = window.Vaani || {};
  window.Vaani.openAddColumnModal = openAddColumnModal;
})();

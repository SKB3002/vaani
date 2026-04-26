/* =====================================================================
   Vaani — settings.js
   Settings page: timezone search, theme, uniques editor (basic).
   ===================================================================== */
(function () {
  "use strict";

  // Ship a short list + lazy fetch full IANA list from server if available.
  const COMMON_TZS = [
    "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo", "Asia/Hong_Kong",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Amsterdam",
    "America/New_York", "America/Chicago", "America/Los_Angeles", "America/Sao_Paulo",
    "Africa/Cairo", "Africa/Johannesburg", "Australia/Sydney", "Pacific/Auckland", "UTC",
  ];

  function initTzCombobox() {
    const input = document.getElementById("tz-input");
    const list = document.getElementById("tz-list");
    if (!input || !list) return;
    function render(filter) {
      const q = (filter || "").toLowerCase();
      const matches = COMMON_TZS.filter(t => t.toLowerCase().includes(q)).slice(0, 8);
      if (!matches.length) {
        list.innerHTML = '<div class="listbox__empty">No matches</div>';
      } else {
        list.innerHTML = matches.map(tz =>
          `<div class="listbox__item" role="option" data-value="${tz}">${tz}</div>`
        ).join("");
      }
      list.hidden = false;
    }
    input.addEventListener("focus", () => render(input.value));
    input.addEventListener("input", () => render(input.value));
    input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 140));
    list.addEventListener("mousedown", (e) => {
      const item = e.target.closest(".listbox__item");
      if (!item) return;
      input.value = item.dataset.value;
      list.hidden = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  // The backend settings endpoint only persists meta.json fields
  // (currency / timezone). API keys + Sheets config live in .env and
  // require a server restart to take effect — flag that to the user
  // instead of silently dropping them.
  const META_FIELDS = new Set(["currency", "timezone", "locale"]);
  const ENV_FIELDS = new Set(["groq_api_key", "sheets_url", "sheets_spreadsheet_id"]);

  async function saveSettings(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const all = {};
    new FormData(form).forEach((v, k) => { all[k] = v; });

    // Build the meta payload (what the backend /api/settings actually accepts).
    const meta = {};
    META_FIELDS.forEach((k) => { if (all[k] !== undefined && all[k] !== "") meta[k] = all[k]; });

    // Inform user about env-only fields they filled in.
    const envFilled = [...ENV_FIELDS].filter((k) => all[k] && String(all[k]).trim());

    try {
      await window.Vaani.api("/api/settings", { method: "PATCH", body: meta });
      if (envFilled.length) {
        window.Vaani.toast({
          type: "info",
          title: "Settings saved",
          message: `Currency/timezone updated. ${envFilled.length} field(s) live in .env — edit the file directly and restart to apply.`,
        });
      } else {
        window.Vaani.toast({ type: "success", message: "Settings saved" });
      }
    } catch (err) {
      window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
    }
  }

  // Theme radio
  function initThemeRadios() {
    document.querySelectorAll("[name='theme']").forEach(r => {
      r.addEventListener("change", () => window.Vaani.setTheme(r.value));
    });
    const currentTheme = document.documentElement.getAttribute("data-theme");
    const target = document.querySelector(`[name='theme'][value='${currentTheme || "auto"}']`);
    if (target) target.checked = true;
  }

  // Uniques editor — very simple add/remove
  function initUniquesEditor() {
    const peopleForm = document.getElementById("uniques-people-form");
    const vendorsForm = document.getElementById("uniques-vendors-form");
    [peopleForm, vendorsForm].forEach(form => {
      if (!form) return;
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const field = form.dataset.field;
        const value = form.querySelector("[name='value']").value.trim();
        if (!value) return;
        try {
          await window.Vaani.api("/api/settings/uniques", {
            method: "POST", body: { field, value },
          });
          window.Vaani.toast({ type: "success", message: `Added to ${field}` });
          // Append chip
          const list = form.previousElementSibling;
          if (list) {
            const chip = document.createElement("span");
            chip.className = "chip";
            chip.textContent = value;
            list.appendChild(chip);
          }
          form.reset();
        } catch (err) {
          window.Vaani.toast({ type: "danger", title: "Could not save", message: err.message });
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTzCombobox();
    initThemeRadios();
    initUniquesEditor();
    const form = document.getElementById("settings-form");
    if (form) form.addEventListener("submit", saveSettings);
  });
})();

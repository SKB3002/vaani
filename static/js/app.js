/* =====================================================================
   Vaani — app.js
   Theme toggle, toast dispatcher, fetch helper, Indian currency format.
   Loaded on every page via base.html.
   ===================================================================== */
(function () {
  "use strict";

  // ------------------------------------------------------------------
  //  Theme (light / dark / auto)
  // ------------------------------------------------------------------
  const STORAGE_THEME = "fineye.theme";
  const root = document.documentElement;

  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") {
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
  }

  const saved = localStorage.getItem(STORAGE_THEME);
  if (saved) applyTheme(saved);

  window.Vaani = window.Vaani || {};
  window.Vaani.toggleTheme = function () {
    const current = root.getAttribute("data-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    let next;
    if (!current) next = prefersDark ? "light" : "dark";
    else next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem(STORAGE_THEME, next);
    // Re-broadcast for charts to re-read colors
    document.dispatchEvent(new CustomEvent("fineye:themechange", { detail: { theme: next } }));
  };

  window.Vaani.setTheme = function (theme) {
    applyTheme(theme);
    if (theme === "auto") localStorage.removeItem(STORAGE_THEME);
    else localStorage.setItem(STORAGE_THEME, theme);
    document.dispatchEvent(new CustomEvent("fineye:themechange", { detail: { theme } }));
  };

  // ------------------------------------------------------------------
  //  Toast dispatcher
  // ------------------------------------------------------------------
  window.Vaani.toast = function (opts) {
    const { title = "", message = "", type = "info", duration = 3600 } = opts || {};
    const stack = document.querySelector(".toast-stack");
    if (!stack) return;
    const el = document.createElement("div");
    el.className = "toast toast--" + type;
    el.setAttribute("role", type === "danger" ? "alert" : "status");
    el.innerHTML =
      '<div class="toast__body">' +
      (title ? '<div class="toast__title"></div>' : "") +
      '<div class="toast__msg"></div>' +
      "</div>";
    if (title) el.querySelector(".toast__title").textContent = title;
    el.querySelector(".toast__msg").textContent = message;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity 220ms ease, transform 220ms ease";
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      setTimeout(() => el.remove(), 240);
    }, duration);
  };

  // ------------------------------------------------------------------
  //  Fetch wrapper (localhost-only, no CSRF needed)
  // ------------------------------------------------------------------
  window.Vaani.api = async function (path, options) {
    options = options || {};
    const init = {
      method: options.method || "GET",
      headers: Object.assign(
        { "Accept": "application/json" },
        options.body ? { "Content-Type": "application/json" } : {},
        options.headers || {}
      ),
    };
    if (options.body !== undefined) {
      init.body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
    }
    const res = await fetch(path, init);
    const ctype = res.headers.get("content-type") || "";
    const data = ctype.includes("application/json") ? await res.json().catch(() => null) : await res.text();
    if (!res.ok) {
      // FastAPI 422 detail is an array of objects {loc, msg, type}.
      let msg = res.statusText || "Request failed";
      if (data && Array.isArray(data.detail)) {
        msg = data.detail.map(d => {
          const field = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
          return field ? `${field}: ${d.msg}` : d.msg;
        }).join(" · ");
      } else if (data && typeof data.detail === "string") {
        msg = data.detail;
      } else if (data && data.message) {
        msg = data.message;
      }
      const err = new Error(msg);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  };

  // ------------------------------------------------------------------
  //  Indian currency formatter (lakhs grouping)
  // ------------------------------------------------------------------
  const inrFormatter = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const inrFormatterShort = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
  window.Vaani.fmtINR = function (n, opts) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    const f = (opts && opts.short) ? inrFormatterShort : inrFormatter;
    return f.format(Number(n));
  };

  // Format plain number (no currency symbol, lakh grouping)
  const inFormatter = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  window.Vaani.fmtNum = function (n) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return inFormatter.format(Number(n));
  };

  // ------------------------------------------------------------------
  //  Topbar wiring — theme toggle button
  // ------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.querySelector("[data-action='toggle-theme']");
    if (toggle) toggle.addEventListener("click", () => window.Vaani.toggleTheme());

    // Voice button wiring lives in /static/js/voice.js (loaded by the partial).
  });
})();

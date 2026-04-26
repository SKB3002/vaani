// M5 — rule-driven chart renderer. Reads registry + payloads from /api/charts.
// Zero chart-specific code: Chart.js config built from server-returned ChartPayload.
(function () {
  "use strict";

  const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
  const NUM = new Intl.NumberFormat("en-IN");
  const PCT = new Intl.NumberFormat("en-IN", { style: "percent", maximumFractionDigits: 1 });

  const charts = new Map(); // chart_id -> Chart instance

  function fmt(val, kind) {
    if (val == null || isNaN(val)) return "—";
    if (kind === "currency") return INR.format(val);
    if (kind === "percent") return PCT.format(val);
    return NUM.format(val);
  }

  function resolveColor(token) {
    if (!token) return null;
    const t = String(token).trim();
    if (t.startsWith("--")) {
      const v = getComputedStyle(document.documentElement).getPropertyValue(t).trim();
      return v || null;
    }
    return t;
  }

  function resolvePalette(list) {
    if (!Array.isArray(list)) return null;
    return list.map(resolveColor).filter(Boolean);
  }

  function defaultPalette(n) {
    const s = getComputedStyle(document.documentElement);
    const base = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => s.getPropertyValue(`--chart-${i}`).trim()).filter(Boolean);
    const out = [];
    for (let i = 0; i < n; i++) out.push(base[i % base.length] || "#888");
    return out;
  }

  function chartJsType(t) {
    if (t === "pie" || t === "donut") return "doughnut";
    if (t === "stacked_bar" || t === "horizontal_bar") return "bar";
    if (t === "area") return "line";
    return t; // bar, line
  }

  // Convert hex/rgb string to rgba with given alpha (for gradient fills).
  function toRgba(color, alpha) {
    if (!color) return `rgba(120,120,120,${alpha})`;
    const c = color.trim();
    if (c.startsWith("#")) {
      const hex = c.length === 4
        ? c.slice(1).split("").map((h) => h + h).join("")
        : c.slice(1);
      const r = parseInt(hex.slice(0, 2), 16);
      const g = parseInt(hex.slice(2, 4), 16);
      const b = parseInt(hex.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${alpha})`;
    }
    if (c.startsWith("rgb")) return c.replace(/rgba?\(([^)]+)\)/, (_, inner) => {
      const parts = inner.split(",").map((s) => s.trim());
      return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`;
    });
    return c;
  }

  // Build a vertical gradient: hex at 30% at top fading to 0% at bottom.
  function verticalGradient(ctx, color) {
    const area = ctx.chart.chartArea;
    if (!area) return toRgba(color, 0.2);
    const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, toRgba(color, 0.30));
    g.addColorStop(1, toRgba(color, 0.00));
    return g;
  }

  // Highlight the peak bar/point in antique gold (max across all datasets).
  function indexOfPeak(datasets) {
    let peakIdx = -1;
    let peakVal = -Infinity;
    datasets.forEach((ds) => {
      (ds.data || []).forEach((v, i) => {
        const n = typeof v === "object" && v !== null ? (v.y ?? v.x) : v;
        if (typeof n === "number" && n > peakVal) { peakVal = n; peakIdx = i; }
      });
    });
    return peakIdx;
  }

  function buildConfig(payload) {
    const fmtKind = (payload.meta && payload.meta.format) || "number";
    const cjsType = chartJsType(payload.type);
    const accent = resolveColor("--chart-accent") || "#C5A059";
    const gridColor = resolveColor("--chart-grid") || "#EAE7E1";
    const labelColor = resolveColor("--chart-label") || "#5C5C5C";
    const tooltipBg = resolveColor("--chart-tooltip-bg") || "#FFFFFF";
    const tooltipBorder = resolveColor("--chart-tooltip-border") || accent;

    // Resolve colors
    const datasets = payload.datasets.map((ds, i) => {
      const out = { ...ds };
      let bg = ds.backgroundColor;
      if (Array.isArray(bg)) {
        const resolved = resolvePalette(bg);
        bg = resolved && resolved.length ? resolved : null;
      } else if (typeof bg === "string") {
        bg = resolveColor(bg);
      }
      if (!bg) {
        if (payload.type === "pie" || payload.type === "donut") {
          bg = defaultPalette(payload.labels.length);
        } else {
          bg = defaultPalette(payload.datasets.length)[i] || "#888";
        }
      }
      out.backgroundColor = bg;
      if (payload.type === "line" || payload.type === "area") {
        const primary = Array.isArray(bg) ? bg[0] : bg;
        out.borderColor = primary;
        out.borderWidth = 2;   // luxury stroke — thin
        out.tension = 0.35;
        out.pointRadius = 0;
        out.pointHoverRadius = 4;
        out.pointHoverBorderColor = accent;
        out.pointHoverBackgroundColor = accent;
        if (payload.type === "area") {
          out.fill = "origin";
          out.backgroundColor = (ctx) => verticalGradient(ctx, primary);
        } else {
          out.fill = false;
        }
      }
      return out;
    });

    // Highlight peak for bar-style charts in antique gold.
    const isBarLike = payload.type === "bar" || payload.type === "horizontal_bar";
    if (isBarLike && datasets.length === 1 && Array.isArray(datasets[0].backgroundColor)) {
      const peak = indexOfPeak(datasets);
      if (peak >= 0) {
        datasets[0].backgroundColor = datasets[0].backgroundColor.map((c, i) => (i === peak ? accent : c));
      }
    } else if (isBarLike && datasets.length === 1 && typeof datasets[0].backgroundColor === "string") {
      const base = datasets[0].backgroundColor;
      const n = (payload.labels || []).length;
      const peak = indexOfPeak(datasets);
      datasets[0].backgroundColor = Array.from({ length: n }, (_, i) => (i === peak ? accent : base));
    }

    const stacked = payload.type === "stacked_bar";
    const horizontal = payload.type === "horizontal_bar";

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: payload.datasets.length > 1 || payload.type === "pie" || payload.type === "donut",
          labels: { color: labelColor, font: { size: 11, weight: "500" }, boxWidth: 10, boxHeight: 10 },
        },
        tooltip: {
          backgroundColor: tooltipBg,
          borderColor: tooltipBorder,
          borderWidth: 1,
          titleColor: labelColor,
          bodyColor: labelColor,
          padding: 10,
          cornerRadius: 4,
          displayColors: true,
          boxPadding: 6,
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed && typeof ctx.parsed === "object"
                ? (horizontal ? ctx.parsed.x : ctx.parsed.y ?? ctx.parsed)
                : ctx.parsed;
              const label = ctx.dataset.label || ctx.label || "";
              return `${label}: ${fmt(v, fmtKind)}`;
            },
          },
        },
      },
      scales: (payload.type === "pie" || payload.type === "donut")
        ? undefined
        : {
            x: {
              stacked,
              grid: { color: gridColor, drawBorder: false },
              ticks: { color: labelColor, autoSkip: true, maxRotation: 0, font: { size: 11 } },
            },
            y: {
              stacked,
              grid: { color: gridColor, drawBorder: false },
              ticks: { color: labelColor, callback: (v) => fmt(v, fmtKind), font: { size: 11 } },
            },
          },
    };

    if (horizontal) {
      options.indexAxis = "y";
    }
    if (payload.type === "donut") {
      options.cutout = "65%";
    }

    return {
      type: cjsType,
      data: { labels: payload.labels, datasets },
      options,
    };
  }

  function renderEmpty(container, payload) {
    container.innerHTML = `
      <div class="empty-state" role="status" style="padding: 2rem 1rem;">
        <div class="empty-state__title">${escapeHtml(payload.title)}</div>
        <p class="empty-state__msg">No data yet for this chart.</p>
      </div>`;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function loadChart(spec, container) {
    try {
      const r = await fetch(`/api/charts/${encodeURIComponent(spec.id)}`, { headers: { Accept: "application/json" } });
      if (!r.ok) {
        container.innerHTML = `<div class="empty-state"><p class="empty-state__msg">Chart failed: ${r.status}</p></div>`;
        return;
      }
      const payload = await r.json();
      if (payload.meta && payload.meta.empty) {
        renderEmpty(container, payload);
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.height = 260;
      canvas.setAttribute("aria-label", payload.title);
      container.innerHTML = "";
      container.appendChild(canvas);
      const existing = charts.get(spec.id);
      if (existing) existing.destroy();
      const cfg = buildConfig(payload);
      // eslint-disable-next-line no-undef
      const chart = new Chart(canvas.getContext("2d"), cfg);
      charts.set(spec.id, chart);
    } catch (err) {
      container.innerHTML = `<div class="empty-state"><p class="empty-state__msg">Chart error: ${escapeHtml(err.message || err)}</p></div>`;
    }
  }

  async function renderAll() {
    const grid = document.getElementById("charts-grid");
    if (!grid) return;
    grid.innerHTML = "";
    let registry;
    try {
      const r = await fetch("/api/charts");
      if (!r.ok) throw new Error(`registry ${r.status}`);
      registry = await r.json();
    } catch (err) {
      grid.innerHTML = `<div class="card"><div class="card__body"><p class="empty-state__msg">Chart registry failed: ${escapeHtml(err.message || err)}</p></div></div>`;
      return;
    }
    if (!registry.charts || registry.charts.length === 0) {
      grid.innerHTML = `<div class="card"><div class="card__body"><p class="empty-state__msg">No charts registered yet. Add entries to <span class="mono">data/meta/charts.yaml</span>.</p></div></div>`;
      return;
    }
    for (const spec of registry.charts) {
      const card = document.createElement("section");
      card.className = "card";
      card.innerHTML = `
        <header class="card__header"><h3 class="card__title">${escapeHtml(spec.title)}</h3></header>
        <div class="card__body" data-chart-id="${escapeHtml(spec.id)}" style="min-height: 260px;"></div>`;
      grid.appendChild(card);
      const body = card.querySelector(".card__body");
      loadChart(spec, body);
    }
  }

  async function refresh() {
    try {
      await fetch("/api/charts/refresh", { method: "POST" });
    } catch (_) { /* swallow — registry may still be in memory */ }
    renderAll();
  }

  document.addEventListener("fineye:themechange", () => {
    // Destroy + re-render to pick up new CSS-var colors
    for (const [, c] of charts) c.destroy();
    charts.clear();
    renderAll();
  });

  window.FinEye = window.FinEye || {};
  window.FinEye.refreshCharts = refresh;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderAll);
  } else {
    renderAll();
  }

  const btn = document.getElementById("charts-refresh-btn");
  if (btn) btn.addEventListener("click", refresh);
  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("charts-refresh-btn");
    if (b) b.addEventListener("click", refresh);
  });
})();

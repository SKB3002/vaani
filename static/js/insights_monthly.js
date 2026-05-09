// Monthly Briefing — fetches /api/insights/monthly, renders narration + stats.
// Mirrors the slug rules and stat-ref key naming from
// app/services/insights/narrator.py::extract_allowed_stat_refs.
(function () {
  "use strict";

  const INR = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  });
  const PCT = new Intl.NumberFormat("en-IN", {
    style: "percent",
    maximumFractionDigits: 1,
  });
  const NUM = new Intl.NumberFormat("en-IN");

  // ---- DOM helpers --------------------------------------------------------

  const $ = (id) => document.getElementById(id);

  function setStatus(msg) {
    const el = $("ib-status");
    if (el) el.textContent = msg || "";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function showBanner(text) {
    const banner = $("ib-banner");
    const t = $("ib-banner-text");
    if (!banner || !t) return;
    if (!text) {
      banner.hidden = true;
      return;
    }
    t.textContent = text;
    banner.hidden = false;
  }

  // ---- Slug + stat-ref map ------------------------------------------------

  function slugify(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  }

  function fmtCurrency(v) {
    if (v == null || isNaN(v)) return "—";
    return INR.format(Number(v));
  }

  // Percentage values from the bundle are already in percent units (0-100),
  // not 0-1 ratios. Format as "%" with one decimal.
  function fmtPercent(v) {
    if (v == null || isNaN(v)) return "—";
    return PCT.format(Number(v) / 100);
  }

  function fmtCount(v) {
    if (v == null || isNaN(v)) return "—";
    return NUM.format(Number(v));
  }

  // Build a flat {key: formattedString} map matching extract_allowed_stat_refs.
  function buildStatRefMap(bundle) {
    const map = {};
    if (!bundle) return map;

    const cm = bundle.current_month || {};
    const pm = bundle.previous_month || {};
    const t3 = bundle.trailing_3m || {};
    const t12 = bundle.trailing_12m || {};

    map["month"] = String(bundle.month || "");
    map["currency"] = String(bundle.currency || "");
    map["current_total"] = fmtCurrency(cm.net_spend);
    map["previous_total"] = fmtCurrency(pm.net_spend);
    map["trailing_3m_total"] = fmtCurrency(t3.net_spend);
    map["trailing_3m_avg"] = fmtCurrency((Number(t3.net_spend) || 0) / 3);
    map["trailing_12m_total"] = fmtCurrency(t12.net_spend);
    map["trailing_12m_avg"] = fmtCurrency((Number(t12.net_spend) || 0) / 12);
    map["txn_count"] = fmtCount(cm.txn_count);
    map["previous_txn_count"] = fmtCount(pm.txn_count);
    map["net_cashflow"] = fmtCurrency(bundle.net_cashflow);

    map["investment_total_current"] = fmtCurrency(bundle.investment_total_current);
    map["investment_total_prev_month"] = fmtCurrency(bundle.investment_total_prev_month);
    map["investment_delta_pct"] = fmtPercent(bundle.investment_delta_pct);
    map["investment_delta_abs"] = fmtCurrency(
      (Number(bundle.investment_total_current) || 0)
      - (Number(bundle.investment_total_prev_month) || 0)
    );

    // Per-category (current month)
    (cm.by_category || []).forEach((c) => {
      const slug = slugify(c.category);
      if (!slug) return;
      map[`${slug}_total`] = fmtCurrency(c.total);
      map[`${slug}_txn_count`] = fmtCount(c.txn_count);
    });

    (bundle.category_deltas_vs_prev || []).forEach((d) => {
      const slug = slugify(d.category);
      if (!slug) return;
      map[`${slug}_delta_abs`] = fmtCurrency(d.delta_abs);
      map[`${slug}_delta_pct`] = fmtPercent(d.delta_pct);
      map[`${slug}_previous`] = fmtCurrency(d.previous);
    });

    (bundle.category_deltas_vs_3m_avg || []).forEach((d) => {
      const slug = slugify(d.category);
      if (!slug) return;
      map[`${slug}_vs_3m_delta_abs`] = fmtCurrency(d.delta_abs);
      map[`${slug}_vs_3m_delta_pct`] = fmtPercent(d.delta_pct);
    });

    // Top merchants (current month, top 5)
    (cm.top_merchants || []).slice(0, 5).forEach((m, i) => {
      const idx = i + 1;
      map[`merchant_${idx}_name`] = String(m.name || "");
      map[`merchant_${idx}_total`] = fmtCurrency(m.total);
      map[`merchant_${idx}_count`] = fmtCount(m.count);
      const slug = slugify(m.name || "");
      if (slug) map[`merchant_${slug}_total`] = fmtCurrency(m.total);
    });

    // Goals
    (bundle.goals || []).forEach((g, i) => {
      const idx = i + 1;
      map[`goal_${idx}_name`] = String(g.goal_name || "");
      map[`goal_${idx}_pct_complete`] = fmtPercent(g.pct_complete);
      map[`goal_${idx}_target`] = fmtCurrency(g.target_amount);
      map[`goal_${idx}_current`] = fmtCurrency(g.current_amount);
      const slug = slugify(g.goal_name || "");
      if (slug) map[`goal_${slug}_pct_complete`] = fmtPercent(g.pct_complete);
    });

    // Budget rows
    (bundle.budget_utilisation || []).forEach((b) => {
      const slug = slugify(b.category);
      if (!slug) return;
      map[`budget_${slug}_budgeted`] = fmtCurrency(b.budgeted);
      map[`budget_${slug}_actual`] = fmtCurrency(b.actual);
      map[`budget_${slug}_remaining`] = fmtCurrency(b.remaining);
      map[`budget_${slug}_utilisation_pct`] = fmtPercent(b.utilisation_pct);
    });

    // Largest transactions
    (bundle.top_n_largest_txns || []).slice(0, 5).forEach((t, i) => {
      const idx = i + 1;
      map[`largest_txn_${idx}_name`] = String(t.expense_name || "");
      map[`largest_txn_${idx}_amount`] = fmtCurrency(t.amount);
    });

    return map;
  }

  // Replace {{stat_ref_key}} placeholders. Unknown keys are left literal so
  // bugs surface visibly in QA rather than silently corrupting prose.
  function rebindRefs(text, refMap) {
    if (!text) return "";
    return String(text).replace(/\{\{([a-z0-9_]+)\}\}/g, (full, key) => {
      if (Object.prototype.hasOwnProperty.call(refMap, key)) {
        return refMap[key];
      }
      return full;
    });
  }

  // ---- Render -------------------------------------------------------------

  const TONE_LABEL = {
    encouraging: "Encouraging",
    neutral: "Neutral",
    warning: "Warning",
  };

  function applyToneStyle(pill, tone) {
    const cls = ["ib-pill"];
    if (tone === "warning") cls.push("ib-pill--warning");
    else if (tone === "encouraging") cls.push("ib-pill--encouraging");
    else cls.push("ib-pill--neutral");
    pill.className = cls.join(" ");
  }

  function renderHeadline(narration, cacheHit, refMap) {
    const card = $("ib-headline");
    const text = $("ib-headline-text");
    const pill = $("ib-tone-pill");
    const cachePill = $("ib-cache-pill");
    if (!card || !text || !pill || !cachePill) return;

    if (!narration) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    text.textContent = rebindRefs(narration.headline || "", refMap);
    const tone = narration.tone || "neutral";
    pill.textContent = TONE_LABEL[tone] || tone;
    applyToneStyle(pill, tone);

    cachePill.textContent = cacheHit ? "✓ cached" : "✦ fresh";
    cachePill.className = "ib-pill ib-pill--cache";
  }

  function renderSections(narration, refMap) {
    const wrap = $("ib-sections");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!narration || !Array.isArray(narration.sections)) return;

    narration.sections.forEach((section) => {
      const card = document.createElement("section");
      card.className = "ib-section";
      const title = document.createElement("h3");
      title.className = "ib-section__title";
      title.textContent = section.title || "";
      const body = document.createElement("p");
      body.className = "ib-section__body";
      // Plaintext insertion via textContent after rebind keeps this XSS-safe;
      // narrator output never contains HTML by contract.
      body.textContent = rebindRefs(section.narrative || "", refMap);
      card.appendChild(title);
      card.appendChild(body);
      wrap.appendChild(card);
    });
  }

  function renderStatsSummary(bundle) {
    const card = $("ib-stats-summary");
    const top = $("ib-stats-top");
    const catsHead = $("ib-cats-head");
    const catsList = $("ib-cats-list");
    const merchHead = $("ib-merch-head");
    const merchList = $("ib-merch-list");
    const goalsHead = $("ib-goals-head");
    const goalsList = $("ib-goals-list");
    if (!card || !top) return;
    if (!bundle) { card.hidden = true; return; }
    card.hidden = false;

    const cm = bundle.current_month || {};
    const monthLabel = formatMonthHuman(bundle.month);
    const cashflow = Number(bundle.net_cashflow || 0);
    const cashflowClass = cashflow > 0
      ? "ib-stat__value ib-stat__value--positive"
      : cashflow < 0
        ? "ib-stat__value ib-stat__value--negative"
        : "ib-stat__value";

    top.innerHTML = "";
    appendStat(top, "Month", monthLabel || "—");
    appendStat(top, "Total spend", fmtCurrency(cm.net_spend));
    appendStat(top, "Transactions", fmtCount(cm.txn_count));
    appendStat(top, "Net cashflow", fmtCurrency(cashflow), cashflowClass);
    appendStat(top, "Previous month", fmtCurrency((bundle.previous_month || {}).net_spend));
    appendStat(top, "Investments (current)", fmtCurrency(bundle.investment_total_current));

    const cats = (cm.by_category || []).slice(0, 5);
    catsHead.hidden = cats.length === 0;
    catsList.innerHTML = "";
    cats.forEach((c) => {
      catsList.appendChild(rowEl(c.category, fmtCurrency(c.total), `${fmtCount(c.txn_count)} txns`));
    });

    const merch = (cm.top_merchants || []).slice(0, 5);
    merchHead.hidden = merch.length === 0;
    merchList.innerHTML = "";
    merch.forEach((m) => {
      merchList.appendChild(rowEl(m.name || "—", fmtCurrency(m.total), `${fmtCount(m.count)} txns`));
    });

    const goals = bundle.goals || [];
    goalsHead.hidden = goals.length === 0;
    goalsList.innerHTML = "";
    goals.forEach((g) => {
      goalsList.appendChild(goalEl(g));
    });
  }

  function appendStat(host, label, value, valueClass) {
    const cell = document.createElement("div");
    cell.className = "ib-stat";
    const k = document.createElement("div");
    k.className = "ib-stat__label";
    k.textContent = label;
    const v = document.createElement("div");
    v.className = valueClass || "ib-stat__value";
    v.textContent = value;
    cell.appendChild(k); cell.appendChild(v);
    host.appendChild(cell);
  }

  function rowEl(name, value, meta) {
    const row = document.createElement("div");
    row.className = "ib-row";
    const n = document.createElement("div");
    n.className = "ib-row__name";
    n.textContent = name;
    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "baseline";
    const v = document.createElement("span");
    v.className = "ib-row__value";
    v.textContent = value;
    const m = document.createElement("span");
    m.className = "ib-row__meta";
    m.textContent = meta || "";
    right.appendChild(v);
    if (meta) right.appendChild(m);
    row.appendChild(n); row.appendChild(right);
    return row;
  }

  function goalEl(g) {
    const wrap = document.createElement("div");
    wrap.className = "ib-goal";
    const head = document.createElement("div");
    head.className = "ib-goal__head";
    const name = document.createElement("span");
    name.className = "ib-goal__name";
    name.textContent = g.goal_name || "—";
    const pct = document.createElement("span");
    pct.className = "ib-goal__pct";
    const pctNum = Math.max(0, Math.min(100, Number(g.pct_complete) || 0));
    pct.textContent = fmtPercent(pctNum);
    head.appendChild(name); head.appendChild(pct);
    const bar = document.createElement("div");
    bar.className = "ib-goal__bar";
    const fill = document.createElement("div");
    fill.className = "ib-goal__fill";
    fill.style.width = `${pctNum}%`;
    bar.appendChild(fill);
    const amounts = document.createElement("div");
    amounts.className = "ib-goal__amounts";
    amounts.textContent = `${fmtCurrency(g.current_amount)} of ${fmtCurrency(g.target_amount)}`;
    wrap.appendChild(head); wrap.appendChild(bar); wrap.appendChild(amounts);
    return wrap;
  }

  function formatMonthHuman(ym) {
    if (!ym) return "";
    const [y, m] = String(ym).split("-").map(Number);
    if (!y || !m) return String(ym);
    const d = new Date(y, m - 1, 1);
    return d.toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  }

  // ---- Reason -> banner text ----------------------------------------------

  const REASON_TEXT = {
    empty_month: "No expenses this month yet.",
    groq_unreachable: "AI narration unavailable — showing numbers only.",
    groq_not_configured: "Set GROQ_API_KEY for narrated insights.",
    contract_violation: "AI output couldn't be validated — showing numbers only.",
    bad_json: "AI output couldn't be validated — showing numbers only.",
    narration_unavailable: "AI narration unavailable — showing numbers only.",
  };

  // ---- Loader -------------------------------------------------------------

  function defaultMonth() {
    const today = new Date();
    let y = today.getFullYear();
    let m = today.getMonth(); // 0-based, this is the *previous* full month
    if (m === 0) {
      y -= 1;
      m = 12;
    }
    return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}`;
  }

  function syncUrl(month) {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("month", month);
      window.history.replaceState({}, "", url.toString());
    } catch (_) { /* best-effort */ }
  }

  function setSkeleton(visible) {
    const sk = $("ib-skeleton");
    if (sk) sk.hidden = !visible;
    // Hide real content while skeleton is up to avoid stale flicker.
    const ids = ["ib-headline", "ib-banner", "ib-stats-summary"];
    ids.forEach((id) => {
      const el = $(id);
      if (el) el.hidden = visible ? true : el.hidden;
    });
    const sections = $("ib-sections");
    if (sections && visible) sections.innerHTML = "";
  }

  async function loadBriefing(month, opts) {
    opts = opts || {};
    const refresh = !!opts.refresh;
    setStatus("");
    showBanner(null);
    setSkeleton(true);

    let resp;
    try {
      const url = `/api/insights/monthly?month=${encodeURIComponent(month)}${refresh ? "&refresh=true" : ""}`;
      resp = await fetch(url, { headers: { Accept: "application/json" } });
    } catch (err) {
      console.error("briefing fetch failed", err);
      setSkeleton(false);
      setStatus("Couldn't load briefing — try again.");
      return;
    }

    if (resp.status === 422) {
      setSkeleton(false);
      setStatus("Invalid month. Pick a valid YYYY-MM.");
      return;
    }
    if (!resp.ok) {
      console.error("briefing http", resp.status);
      setSkeleton(false);
      setStatus("Couldn't load briefing — try again.");
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      console.error("briefing parse", err);
      setSkeleton(false);
      setStatus("Couldn't read briefing response.");
      return;
    }

    setSkeleton(false);
    setStatus("");
    const bundle = data.stats_bundle;
    const narration = data.narration;
    const cacheHit = !!data.cache_hit;
    const reason = data.reason;

    const refMap = buildStatRefMap(bundle);
    renderHeadline(narration, cacheHit, refMap);
    renderSections(narration, refMap);
    renderStatsSummary(bundle);

    if (!narration) {
      const text = REASON_TEXT[reason] || REASON_TEXT.narration_unavailable;
      showBanner(text);
    } else {
      showBanner(null);
    }
  }

  // ---- Boot ---------------------------------------------------------------

  function init() {
    const params = new URLSearchParams(window.location.search);
    const initialMonth = params.get("month") || defaultMonth();

    const input = $("ib-month");
    if (input) {
      input.value = initialMonth;
      input.addEventListener("change", () => {
        const m = input.value;
        if (!m) return;
        syncUrl(m);
        loadBriefing(m);
      });
    }

    const regen = $("ib-regenerate");
    if (regen) {
      regen.addEventListener("click", () => {
        const m = (input && input.value) || initialMonth;
        loadBriefing(m, { refresh: true });
      });
    }

    syncUrl(initialMonth);
    loadBriefing(initialMonth);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

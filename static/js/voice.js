/* =====================================================================
   Vaani — voice.js
   Push-to-talk → parse → editable review panel → confirm → save.
   ===================================================================== */
(function () {
  "use strict";

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  const TYPE_CATEGORIES = [
    "Need, Food & Drinks", "Need, Travel", "Need, Enjoyment", "Need, Miscellaneous",
    "Want, Food & Drinks", "Want, Travel", "Want, Enjoyment", "Want, Miscellaneous",
    "Investment, Food & Drinks", "Investment, Travel", "Investment, Enjoyment", "Investment, Miscellaneous",
  ];
  const PAYMENTS = ["paid", "paid_cash", "paid_by", "paid_for", "adjusted"];
  const PAYMENT_LABELS = {
    paid: "Paid (online/UPI)",
    paid_cash: "Paid Cash",
    paid_by: "Paid By someone",
    paid_for: "Paid For someone",
    adjusted: "Balance Adjust",
  };

  // ------------------------------------------------------------------ helpers

  function setState(btn, state) {
    if (!btn) return;
    btn.setAttribute("data-state", state);
    btn.classList.toggle("btn--voice-recording", state === "recording");
  }

  function setTranscript(text) {
    document.querySelectorAll("[data-voice-transcript]").forEach((el) => {
      el.textContent = text || "";
    });
  }

  // ------------------------------------------------------------------ review panel

  function buildItemCard(item, idx) {
    const card = document.createElement("div");
    card.className = "voice-review-card";
    card.dataset.idx = idx;

    // Expense name
    const nameRow = `
      <div class="field" style="flex:1;min-width:140px;">
        <label class="field__label" for="vr-name-${idx}">Expense</label>
        <input class="input" id="vr-name-${idx}" data-field="expense_name"
               value="${_esc(item.expense_name)}" placeholder="What was it?">
      </div>`;

    // Amount
    const amtRow = `
      <div class="field" style="width:110px;">
        <label class="field__label" for="vr-amt-${idx}">Amount (₹)</label>
        <input class="input mono" id="vr-amt-${idx}" type="number" min="0.01" step="0.01"
               data-field="amount" value="${item.amount || ""}">
      </div>`;

    // Type + Category
    const tcOpts = TYPE_CATEGORIES.map(
      (v) => `<option value="${v}" ${v === item.type_category ? "selected" : ""}>${v}</option>`
    ).join("");
    const tcRow = `
      <div class="field" style="flex:1;min-width:180px;">
        <label class="field__label" for="vr-tc-${idx}">Type, Category</label>
        <select class="input" id="vr-tc-${idx}" data-field="type_category">${tcOpts}</select>
      </div>`;

    // Payment method
    const pmOpts = PAYMENTS.map(
      (v) => `<option value="${v}" ${v === item.payment_method ? "selected" : ""}>${PAYMENT_LABELS[v] || v}</option>`
    ).join("");
    const pmRow = `
      <div class="field" style="width:160px;">
        <label class="field__label" for="vr-pm-${idx}">Payment</label>
        <select class="input" id="vr-pm-${idx}" data-field="payment_method">${pmOpts}</select>
      </div>`;

    card.innerHTML = `
      <div class="voice-review-card__head">
        <span class="voice-review-card__num">${idx + 1}</span>
        <button type="button" class="btn btn--ghost btn--icon voice-review-card__del"
                aria-label="Remove this item" data-del="${idx}">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div class="voice-review-card__fields">
        ${nameRow}${amtRow}${tcRow}${pmRow}
      </div>`;
    return card;
  }

  function showReviewPanel(parseResult) {
    // Remove any existing panel
    document.getElementById("voice-review-panel")?.remove();

    const isATM = parseResult.action === "atm_transfer";
    const items = isATM ? [] : (parseResult.items || []);

    const panel = document.createElement("div");
    panel.id = "voice-review-panel";
    panel.className = "voice-review-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Review parsed expenses");

    // Build item list container
    const listEl = document.createElement("div");
    listEl.className = "voice-review-list";

    // ATM preview is a single summary row, not editable cards
    if (isATM) {
      listEl.innerHTML = `
        <div class="voice-review-atm">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="1" y="4" width="22" height="16" rx="2" ry="2"/>
            <line x1="1" y1="10" x2="23" y2="10"/>
          </svg>
          <div>
            <div class="voice-review-atm__label">ATM Withdrawal</div>
            <div class="voice-review-atm__amount">${window.Vaani.fmtINR(parseResult.atm_amount)}</div>
          </div>
        </div>`;
    } else {
      items.forEach((item, idx) => listEl.appendChild(buildItemCard(item, idx)));
    }

    // Count badge
    const countText = isATM
      ? "ATM withdrawal"
      : `${items.length} expense${items.length !== 1 ? "s" : ""} parsed`;

    const tzLabel = parseResult.timezone
      ? `<span class="voice-review-tz">${_esc(parseResult.timezone)}</span>`
      : "";

    panel.innerHTML = `
      <div class="voice-review-panel__backdrop"></div>
      <div class="voice-review-panel__sheet">
        <div class="voice-review-panel__head">
          <div>
            <div class="voice-review-panel__title">Review before saving</div>
            <div class="voice-review-panel__sub">${_esc(countText)}</div>
          </div>
          <button type="button" class="btn btn--ghost btn--icon" id="vr-close" aria-label="Cancel">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div class="voice-review-panel__date-row">
          <div class="field" style="margin:0;">
            <label class="field__label" for="vr-date" style="display:flex;align-items:center;gap:var(--sp-2);">
              Date ${tzLabel}
            </label>
            <input class="input" id="vr-date" type="date" value="${_esc(parseResult.date || "")}"
                   style="width:180px;">
          </div>
        </div>
        <div class="voice-review-panel__body"></div>
        <div class="voice-review-panel__foot">
          <button type="button" class="btn" id="vr-cancel">Cancel</button>
          <button type="button" class="btn btn--primary" id="vr-save">
            ${isATM ? "Confirm ATM withdrawal" : `Save ${items.length} expense${items.length !== 1 ? "s" : ""}`}
          </button>
        </div>
      </div>`;

    panel.querySelector(".voice-review-panel__body").appendChild(listEl);
    document.body.appendChild(panel);

    // Trap focus inside
    requestAnimationFrame(() => {
      const firstInput = panel.querySelector("input, select, button");
      if (firstInput) firstInput.focus();
    });

    // Wire close
    function closePanel() { panel.remove(); }
    panel.querySelector("#vr-close").addEventListener("click", closePanel);
    panel.querySelector("#vr-cancel").addEventListener("click", closePanel);
    panel.querySelector(".voice-review-panel__backdrop").addEventListener("click", closePanel);

    // Wire delete buttons
    panel.addEventListener("click", (e) => {
      const delBtn = e.target.closest("[data-del]");
      if (!delBtn) return;
      const idx = parseInt(delBtn.dataset.del, 10);
      const card = panel.querySelector(`[data-idx="${idx}"]`);
      if (card) card.remove();
      // Re-number remaining cards
      panel.querySelectorAll(".voice-review-card").forEach((c, i) => {
        c.dataset.idx = i;
        const num = c.querySelector(".voice-review-card__num");
        if (num) num.textContent = i + 1;
        c.querySelectorAll("[data-del]").forEach((b) => b.dataset.del = i);
        c.querySelectorAll("[id]").forEach((el) => {
          el.id = el.id.replace(/-\d+$/, `-${i}`);
        });
      });
      // Update save button label
      const remaining = panel.querySelectorAll(".voice-review-card").length;
      const saveBtn = panel.querySelector("#vr-save");
      saveBtn.textContent = `Save ${remaining} expense${remaining !== 1 ? "s" : ""}`;
    });

    // Wire save
    panel.querySelector("#vr-save").addEventListener("click", async () => {
      let confirmPayload;

      const chosenDate = panel.querySelector("#vr-date")?.value || parseResult.date;

      if (isATM) {
        confirmPayload = {
          action: "atm_transfer",
          atm_amount: parseResult.atm_amount,
          raw_transcript: parseResult.raw_transcript || null,
        };
      } else {
        // Collect current field values from all remaining cards
        const cards = panel.querySelectorAll(".voice-review-card");
        if (!cards.length) { closePanel(); return; }

        const confirmedItems = [];
        let valid = true;
        cards.forEach((card) => {
          const get = (field) => card.querySelector(`[data-field="${field}"]`)?.value?.trim() || "";
          const name = get("expense_name");
          const amount = parseFloat(get("amount"));
          const tc = get("type_category");
          const pm = get("payment_method");
          if (!name || !amount || !tc || !pm) { valid = false; return; }
          confirmedItems.push({
            expense_name: name,
            amount,
            type_category: tc,
            payment_method: pm,
            date: chosenDate,
          });
        });

        if (!valid) {
          window.Vaani.toast({ type: "danger", message: "Fill in all fields before saving." });
          return;
        }
        confirmPayload = {
          action: "expense",
          items: confirmedItems,
          raw_transcript: parseResult.raw_transcript || null,
        };
      }

      const saveBtn = panel.querySelector("#vr-save");
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";

      try {
        const res = await window.Vaani.api("/api/expense/confirm", {
          method: "POST",
          body: confirmPayload,
        });
        closePanel();

        if (res.status === "atm_transfer") {
          const b = res.balances || {};
          window.Vaani.toast({
            type: "success",
            title: "ATM withdrawal recorded",
            message: `Cash ${window.Vaani.fmtINR(b.cash_balance)} · Online ${window.Vaani.fmtINR(b.online_balance)}`,
          });
          document.dispatchEvent(new CustomEvent("fineye:balance-changed", { detail: b }));
        } else {
          const rows = res.rows || [];
          if (rows.length === 1) {
            window.Vaani.toast({
              type: "success",
              title: "Expense saved",
              message: `${window.Vaani.fmtINR(rows[0].amount)} · ${rows[0].expense_name}`,
            });
          } else {
            const total = rows.reduce((s, r) => s + (r.amount || 0), 0);
            window.Vaani.toast({
              type: "success",
              title: `${rows.length} expenses saved`,
              message: `Total ${window.Vaani.fmtINR(total)}`,
            });
          }
          rows.forEach((r) => document.dispatchEvent(new CustomEvent("fineye:expense-added", { detail: r })));
        }
      } catch (err) {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save";
        window.Vaani.toast({ type: "danger", title: "Save failed", message: err.message });
      }
    });
  }

  // ------------------------------------------------------------------ transcript submission

  async function submitTranscript(transcript) {
    if (!transcript || !transcript.trim()) return;
    try {
      const res = await window.Vaani.api("/api/expense/parse", {
        method: "POST",
        body: { transcript },
      });
      handleParseResult(res, transcript);
    } catch (err) {
      if (err.status === 422) {
        window.Vaani.toast({ type: "danger", title: "Could not parse", message: "Please try again." });
      } else if (err.status === 503) {
        window.Vaani.toast({ type: "danger", title: "Voice service unavailable", message: "Set GROQ_API_KEY." });
      } else {
        window.Vaani.toast({ type: "danger", title: "Voice error", message: err.message });
      }
    }
  }

  function handleParseResult(res, rawTranscript) {
    if (!res || !res.status) return;

    if (res.status === "clarify") {
      const extra = window.prompt(res.question || "Please clarify:", rawTranscript || "");
      if (extra && extra.trim()) submitTranscript(`${rawTranscript} ${extra}`.trim());
      return;
    }

    if (res.status === "preview") {
      // Attach the raw transcript so confirm can store it
      res.raw_transcript = rawTranscript;
      showReviewPanel(res);
    }
  }

  // ------------------------------------------------------------------ speech recognition

  // Pick the best MediaRecorder mime type the browser supports.
  // Chrome/Firefox/Edge prefer Opus-in-WebM; Safari needs MP4.
  function pickAudioMime() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/mp4",
      "audio/ogg;codecs=opus",
    ];
    if (typeof MediaRecorder === "undefined") return null;
    for (const m of candidates) {
      if (MediaRecorder.isTypeSupported(m)) return m;
    }
    return ""; // let the browser pick its default
  }

  // Upload the recorded audio blob to /api/voice/transcribe.
  // Bounded by `timeoutMs` — if Whisper is slow, we let the caller fall back
  // to the live-typed transcript instead of blocking the user.
  async function whisperTranscribe(blob, mime, timeoutMs = 6000) {
    const ext = (mime || "").includes("mp4") ? "mp4"
              : (mime || "").includes("ogg") ? "ogg"
              : "webm";
    const fd = new FormData();
    fd.append("audio", blob, `clip.${ext}`);
    // Omit `language` → Whisper auto-detects (handles Hindi/Hinglish).

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const resp = await fetch("/api/voice/transcribe", {
        method: "POST",
        body: fd,
        signal: ctrl.signal,
      });
      if (!resp.ok) return null;
      const j = await resp.json().catch(() => null);
      const text = j && typeof j.text === "string" ? j.text.trim() : "";
      return text || null;
    } catch (_) {
      return null; // network error / abort → caller falls back
    } finally {
      clearTimeout(timer);
    }
  }

  function attachWebSpeech(btn) {
    let rec = null;
    let finalText = "";
    let active = false;

    // Parallel MediaRecorder for the Whisper accuracy pass.
    let mediaStream = null;
    let mediaRec = null;
    let audioChunks = [];
    let audioMime = "";

    async function startMediaRecorder() {
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (_) {
        return; // mic denied — Whisper pass simply won't run; browser STT still works
      }
      audioChunks = [];
      audioMime = pickAudioMime() || "";
      try {
        mediaRec = audioMime
          ? new MediaRecorder(mediaStream, { mimeType: audioMime })
          : new MediaRecorder(mediaStream);
      } catch (_) {
        stopMediaStream();
        return;
      }
      mediaRec.ondataavailable = (e) => { if (e.data && e.data.size) audioChunks.push(e.data); };
      mediaRec.start();
    }

    function stopMediaStream() {
      if (mediaStream) {
        try { mediaStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
        mediaStream = null;
      }
    }

    // Returns a Blob of the recorded audio, or null if recording wasn't running.
    function finishMediaRecorder() {
      return new Promise((resolve) => {
        if (!mediaRec || mediaRec.state === "inactive") {
          stopMediaStream();
          resolve(null);
          return;
        }
        mediaRec.onstop = () => {
          const blob = audioChunks.length
            ? new Blob(audioChunks, { type: audioMime || "audio/webm" })
            : null;
          stopMediaStream();
          resolve(blob);
        };
        try { mediaRec.stop(); } catch (_) { stopMediaStream(); resolve(null); }
      });
    }

    function start() {
      if (active) return;
      active = true;
      finalText = "";
      setTranscript("Listening…");
      setState(btn, "recording");
      // Kick off audio capture in parallel — best-effort, never blocks browser STT.
      startMediaRecorder();
      try {
        rec = new SR();
        rec.lang = "en-IN";
        rec.continuous = false;
        rec.interimResults = true;
        rec.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const chunk = e.results[i][0].transcript;
            if (e.results[i].isFinal) finalText += chunk;
            else interim += chunk;
          }
          setTranscript((finalText + " " + interim).trim());
        };
        rec.onerror = (e) => {
          window.Vaani.toast({ type: "danger", title: "Mic error", message: e.error || "unknown" });
          stop();
        };
        rec.onend = async () => {
          setState(btn, "idle");
          active = false;
          const browserText = finalText.trim();
          // Wait for the parallel recording, then ask Whisper for a cleaner pass.
          const blob = await finishMediaRecorder();
          let finalTranscript = browserText;
          if (blob && blob.size > 1000) {
            const whisperText = await whisperTranscribe(blob, audioMime);
            if (whisperText) {
              finalTranscript = whisperText;
              if (whisperText !== browserText) {
                setTranscript(whisperText); // show the corrected version
              }
            }
          }
          if (finalTranscript) {
            setTranscript(finalTranscript);
            submitTranscript(finalTranscript);
          } else {
            setTranscript("");
          }
        };
        rec.start();
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Voice error", message: err.message });
        setState(btn, "idle");
        active = false;
        stopMediaStream();
      }
    }

    function stop() {
      if (rec && active) { try { rec.stop(); } catch (_) {} }
    }

    btn.addEventListener("mousedown",  (e) => { e.preventDefault(); start(); });
    btn.addEventListener("touchstart", (e) => { e.preventDefault(); start(); }, { passive: false });
    btn.addEventListener("mouseup",    () => stop());
    btn.addEventListener("mouseleave", () => stop());
    btn.addEventListener("touchend",   () => stop());
    btn.addEventListener("keydown",    (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); start(); } });
    btn.addEventListener("keyup",      (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); stop(); } });
  }

  // Whisper-only path for browsers without SpeechRecognition (Firefox, etc.).
  // No live typing, but the user still gets accurate voice → parse.
  function attachWhisperOnly(btn) {
    let stream = null;
    let recorder = null;
    let chunks = [];
    let mime = "";
    let active = false;

    async function start() {
      if (active) return;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        window.Vaani.toast({ type: "danger", title: "Mic error", message: err.message });
        return;
      }
      active = true;
      chunks = [];
      mime = pickAudioMime() || "";
      try {
        recorder = mime
          ? new MediaRecorder(stream, { mimeType: mime })
          : new MediaRecorder(stream);
      } catch (err) {
        active = false;
        try { stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
        window.Vaani.toast({ type: "danger", title: "Recorder error", message: err.message });
        return;
      }
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.start();
      setTranscript("Listening…");
      setState(btn, "recording");
    }

    function stop() {
      if (!active || !recorder) return;
      active = false;
      setState(btn, "idle");
      recorder.onstop = async () => {
        try { stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
        const blob = chunks.length ? new Blob(chunks, { type: mime || "audio/webm" }) : null;
        if (!blob || blob.size < 1000) { setTranscript(""); return; }
        setTranscript("Transcribing…");
        const text = await whisperTranscribe(blob, mime, 15000);
        if (text) { setTranscript(text); submitTranscript(text); }
        else {
          setTranscript("");
          window.Vaani.toast({ type: "danger", title: "Transcription failed", message: "Try again." });
        }
      };
      try { recorder.stop(); } catch (_) {}
    }

    btn.addEventListener("mousedown",  (e) => { e.preventDefault(); start(); });
    btn.addEventListener("touchstart", (e) => { e.preventDefault(); start(); }, { passive: false });
    btn.addEventListener("mouseup",    () => stop());
    btn.addEventListener("mouseleave", () => stop());
    btn.addEventListener("touchend",   () => stop());
    btn.addEventListener("keydown",    (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); start(); } });
    btn.addEventListener("keyup",      (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); stop(); } });
  }

  function attachFallback(btn) {
    btn.addEventListener("click", () => {
      const text = window.prompt("Enter expense (voice not supported in this browser):");
      if (text && text.trim()) submitTranscript(text.trim());
    });
  }

  function _esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // Expose submitTranscript so the text-input fallback can call it
  window.Vaani = window.Vaani || {};
  window.Vaani.submitTranscript = submitTranscript;

  document.addEventListener("DOMContentLoaded", () => {
    const buttons = document.querySelectorAll(".btn--voice, [data-voice-ptt]");
    const hasMediaRecorder = typeof MediaRecorder !== "undefined" &&
      navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    buttons.forEach((btn) => {
      if (SR) attachWebSpeech(btn);              // Chrome/Edge/Safari — live typing + Whisper upgrade
      else if (hasMediaRecorder) attachWhisperOnly(btn); // Firefox — Whisper only
      else attachFallback(btn);                   // ancient browser → text prompt
    });
  });
})();

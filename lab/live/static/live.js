/* The Live Lab page: start an experiment, show what the server sees while it runs, then replay the recording
   with the documentation's own widgets (adaptive-lab.js). Every number here comes from the local server's event
   log or, at the end, from Machyon's Report in the recording; nothing is simulated. */
(() => {
  "use strict";
  const token = document.querySelector('meta[name="lab-token"]').content;
  const $ = (id) => document.getElementById(id);
  const form = $("settings"), note = $("note"), go = $("go");
  let stage = null, inst = null, poll = 0, after = 0, runIndex = -1, points = [], ok = 0, throttled = 0, ceiling = null, lastUrl = null;
  let open = new Map(), freeSlots = [], freeDoor = [], nextSlot = 0, nextDoor = 0, rtt = 100, recent = [];

  async function api(path, body) {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: Object.assign({ "X-Lab-Token": token }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // ---------------------------------------------------------------- theme
  const themeBtn = $("theme");
  function setTheme(dark) {
    document.body.setAttribute("data-md-color-scheme", dark ? "slate" : "default");
    themeBtn.textContent = dark ? "Light" : "Dark";
  }
  setTheme(window.matchMedia("(prefers-color-scheme: dark)").matches);
  themeBtn.addEventListener("click", () => setTheme(document.body.getAttribute("data-md-color-scheme") !== "slate"));

  // ---------------------------------------------------------------- settings
  form.addEventListener("change", () => { $("custom").hidden = form.preset.value !== "custom"; });
  const val = (name) => Number(form[name].value);

  function runs() {
    const c = val("ceiling") || 24;   // with no server limit, the presets use 24 as their yardstick
    const fixed = (name, n, label) => ({ name, label, adaptive: false, concurrency: n, max_concurrency: Math.max(2, n) });
    const adaptive = { name: "adaptive", label: "Adaptive, starting at 4", adaptive: true, concurrency: 4, max_concurrency: 256 };
    if (form.preset.value === "adaptive") return [adaptive];
    if (form.preset.value === "custom") {
      const on = form.c_adaptive.value === "true";
      const n = val("c_concurrency");
      return [{ name: "custom", label: on ? `Adaptive, starting at ${n}, at most ${val("c_max")}` : `Fixed at ${n}`,
        adaptive: on, concurrency: n, max_concurrency: Math.max(n, val("c_max"), 2) }];
    }
    const low = Math.max(1, Math.round(c / 3));
    return [fixed("fixed-low", low, `Fixed at ${low}`), fixed("fixed-high", Math.min(256, c * 2), `Fixed at ${Math.min(256, c * 2)}`),
      adaptive, fixed("fixed-at-limit", c, `Fixed at ${c}, the limit`)];
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    rtt = val("rtt_ms") || 100;
    const settings = { images: val("images"), ceiling: val("ceiling"), rtt_ms: val("rtt_ms"),
      p503_percent: val("p503_percent"), runs: runs() };
    note.classList.remove("error");
    note.textContent = "Starting...";
    go.disabled = true;
    try {
      await api("/api/runs", settings);
    } catch (e) {
      note.textContent = e.message; note.classList.add("error"); go.disabled = false;
      return;
    }
    $("result").hidden = true;
    $("now").hidden = false;
    runIndex = -1;
    poll = window.setTimeout(tick, 200);
  });

  // ---------------------------------------------------------------- while it runs
  function clearTracking() {
    after = 0; points = []; ok = 0; throttled = 0; recent = [];
    open = new Map(); freeSlots = []; freeDoor = []; nextSlot = 0; nextDoor = 0;
  }
  function resetRun(status) {
    runIndex = status.run_index; clearTracking();
    ceiling = status.server_ceiling;
    // the same stage and instruments as the recorded replay; the door holds up to twice the limit
    const slots = ceiling || 48, door = ceiling ? Math.min(64, ceiling) : 0;
    stage = window.MachyonLab.createStage({ slots, door, limitLabel: ceiling ? `limit ${ceiling}` : null });
    inst = window.MachyonLab.createInstruments({ cap: ceiling, scaleMax: (ceiling || 24) * 2, total: val("images") });
    $("now-stage").replaceChildren(stage.el, inst.el);
    $("now-run").textContent = `Run ${status.run_index + 1} of ${status.runs}: ${status.run_label}`;
  }

  // each request gets a place on the stage as it arrives, by the server's own rule: over the limit when the server
  // counted more than `ceiling` in flight (it answers those with 429)
  const take = (pool) => pool.sort((a, b) => b - a).pop();
  function track(e, delay) {
    if (e.event === "start") {
      const over = Boolean(ceiling) && e.inflight > ceiling;
      const place = over ? (freeDoor.length ? take(freeDoor) : nextDoor++) : (freeSlots.length ? take(freeSlots) : nextSlot++);
      open.set(e.id, { s: e.t_ms, over, place });
      stage.spawn(over ? "reqOver" : "req", place, delay);
    } else {
      const r = open.get(e.id);
      if (!r) return;
      open.delete(e.id);
      (r.over ? freeDoor : freeSlots).push(r.place);
      const a = window.MachyonLab.answerFor({ over: r.over, status: e.status, outcome: e.outcome });  // same rules as the replay
      stage.answer(a.from, r.place, a.kind, delay);
    }
  }

  async function tick() {
    try {
      const status = await api("/api/runs/current");
      if (status.run_index !== runIndex && status.run_index >= 0) resetRun(status);
      if (status.run_index >= 0) {
        const page = await api(`/api/runs/current/events?after=${after}`);
        if (page.run_index === runIndex) {
          if (page.latest && page.latest < after) clearTracking();  // server reset
          const first = page.events.length ? page.events[0].t_ms : 0;
          for (const e of page.events) {
            points.push([e.t_ms, e.inflight]);
            if (e.event === "end" && e.status === 200 && e.outcome === "complete") ok += 1;
            if (e.event === "end" && e.status === 429) { throttled += 1; recent.push(e.t_ms); }
            track(e, Math.min(300, e.t_ms - first));   // spread this batch's dots over the time it covered
          }
          after = page.next;
          draw();
        }
      }
      note.textContent = `${status.state === "running" ? "Running" : status.state}: ${status.elapsed_s} s`;
      if (status.state === "done") return finished();
      if (status.state === "error") {
        note.textContent = status.error; note.classList.add("error"); go.disabled = false;
        return;
      }
    } catch (e) {
      note.textContent = e.message; note.classList.add("error");
    }
    poll = window.setTimeout(tick, 300);
  }

  function draw() {
    const n = points.length ? points[points.length - 1][1] : 0, tNow = points.length ? points[points.length - 1][0] : 0;
    recent = recent.filter((t) => t > tNow - 500);
    inst.set({ inflight: n, delivered: ok, throttled, recent: recent.length });
    const busy = [];
    let overs = 0;
    for (const r of open.values()) {
      if (r.over) overs++;
      else busy.push({ slot: r.place, p: Math.min(0.95, (tNow - r.s) / rtt), kind: "ok" });
    }
    stage.set(busy, overs, `${(tNow / 1000).toFixed(1)} s`);
    const canvas = $("now-chart"), dpr = window.devicePixelRatio || 1, w = canvas.clientWidth || 600, h = 200;
    canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d"), cs = getComputedStyle(document.body);
    const c = (name) => cs.getPropertyValue(name).trim();
    ctx.scale(dpr, dpr);
    const tMax = Math.max(5000, points.length ? points[points.length - 1][0] : 0);
    const yMax = Math.max(8, ceiling || 0, ...points.map((p) => p[1])) * 1.15;
    const x = (t) => 36 + t / tMax * (w - 48), y = (v) => 10 + (1 - v / yMax) * (h - 34);
    ctx.font = `11px ${c("--md-text-font-family") || "sans-serif"}`;
    ctx.fillStyle = c("--lab-muted");
    ctx.fillText(`${Math.round(tMax / 1000)} s`, w - 30, h - 6);
    ctx.fillText("0", 6, y(0) + 4);
    if (ceiling) {
      ctx.save(); ctx.setLineDash([5, 4]); ctx.strokeStyle = c("--lab-over");
      ctx.beginPath(); ctx.moveTo(36, y(ceiling)); ctx.lineTo(w - 12, y(ceiling)); ctx.stroke(); ctx.restore();
      ctx.fillStyle = c("--lab-over"); ctx.fillText(`limit ${ceiling}`, 6, y(ceiling) + 4);
    }
    // the most in flight per 100 ms, drawn as steps: the same reading as the recorded chart
    const BIN = 100, peak = new Map(), endOf = new Map();
    for (const [t, v] of points) {
      const k = Math.floor(t / BIN);
      peak.set(k, Math.max(peak.has(k) ? peak.get(k) : 0, v));
      endOf.set(k, v);
    }
    ctx.strokeStyle = c("--lab-signal"); ctx.lineWidth = 2; ctx.lineJoin = "round";
    ctx.beginPath();
    const lastK = points.length ? Math.floor(points[points.length - 1][0] / BIN) : -1;
    let carry = 0;   // in flight when the previous bin ended; a bin's peak includes what it started with
    for (let k = 0; k <= lastK; k++) {
      const v = peak.has(k) ? Math.max(peak.get(k), carry) : carry;
      if (k === 0) ctx.moveTo(x(0), y(v)); else ctx.lineTo(x(k * BIN), y(v));
      ctx.lineTo(x((k + 1) * BIN), y(v));
      if (endOf.has(k)) carry = endOf.get(k);
    }
    ctx.stroke();
  }

  // ---------------------------------------------------------------- when it's done
  async function finished() {
    try {
      const doc = await api("/api/runs/current/recording");
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = URL.createObjectURL(new Blob([JSON.stringify(doc)], { type: "application/json" }));
      const slot = $("result-slot");
      const make = (attr) => { const d = document.createElement("div"); d.className = attr.replace("data-lab", "mx-lab");
        d.setAttribute(attr, ""); d.dataset.recording = lastUrl; return d; };
      const link = document.createElement("a");
      link.href = lastUrl; link.download = "machyon-lab-recording.json"; link.textContent = "Download this recording (JSON)";
      slot.replaceChildren(link, make("data-lab-replay"), make("data-lab-compare"), make("data-lab-provenance"));
      $("result").hidden = false;
      window.MachyonLab.mountAll();
      note.textContent = "Done. Change a setting and run it again to compare.";
    } catch (e) {
      note.textContent = e.message; note.classList.add("error");
    }
    go.disabled = false;
  }
})();

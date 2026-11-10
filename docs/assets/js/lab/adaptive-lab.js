/* Adaptive Concurrency Lab (docs/lab/adaptive-concurrency.md), and the Live app (lab/live) that reuses it.

   Widgets, each mounted on an element with a data attribute:
     [data-lab-model]       an idealised server, labelled as a model: nothing in it is measured
     [data-lab-predict]     a prediction whose answer is computed from the recording, never hard-coded
     [data-lab-replay]      a replay of the recorded server events (what the server saw), plus Machyon's own Report
     [data-lab-compare]     the runs side by side
     [data-lab-provenance]  where and how the recording was made

   The stage is the one bold element: a night-time window onto the server. Machyon sits on the left, the server's
   slots on the right, and the dots between them are requests going out (lavender), images coming back (matcha) and
   requests turned away (coral). In the replay every slot and every count comes from the recorded events; the dots
   are drawn for the events the playhead crosses (thinned out at high speeds, so the counters are the exact record).

   Numbers measured at the server and numbers reported by Machyon are labelled and never drawn on the same axis.
   Needs recording.js (MachyonLab.loadRecording / eventsOf). No network beyond this site, no dependencies. */
(() => {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const SCRIPT = document.currentScript && document.currentScript.src;
  const asset = (path) => new URL(path, SCRIPT || document.baseURI).href;
  const OPA_NIGHT = asset("../../brand/opa-symbol-dark.svg");     // white body: for the night stage, in both schemes
  const OPA_LIGHT = asset("../../brand/opa-symbol-light.svg");
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Moving requests follow the system's "reduce motion" setting until the reader chooses; the choice is remembered.
  // (Windows turns that setting on whenever "Animation effects" is off, so the stage offers a visible switch.)
  const MOTION_KEY = "machyon-lab-motion";
  const motionChoice = () => { try { return window.localStorage.getItem(MOTION_KEY); } catch (e) { return null; } };
  const motionOn = () => { const c = motionChoice(); return c ? c === "on" : !reduceMotion(); };
  function setMotion(on) {
    try { window.localStorage.setItem(MOTION_KEY, on ? "on" : "off"); } catch (e) { /* private mode: this page only */ }
    memo = on;
    window.dispatchEvent(new CustomEvent("mx-lab-motion"));
  }
  let memo = null;   // when storage is unavailable, the choice still holds for this page
  const moving = () => (memo === null ? motionOn() : memo);
  const cache = new Map();
  const controllers = new Map();   // recording url -> replay controller, so the prediction can drive the replay

  // ------------------------------------------------------------------ small helpers
  function el(tag, attrs = {}, ...kids) {
    const n = tag.startsWith("svg:") ? document.createElementNS(SVGNS, tag.slice(4)) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) n.append(kid);
    return n;
  }
  const num = (v, d = 0) => (v === null || v === undefined ? "–" :
    Number(v).toLocaleString("en", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const secs = (ms) => `${num(ms / 1000, 1)} s`;
  const urlOf = (node) => new URL(node.dataset.recording, document.baseURI).href;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const ease = (x) => (x < 0.5 ? 2 * x * x : 1 - (-2 * x + 2) ** 2 / 2);

  function recording(node) {
    const url = urlOf(node);
    if (!cache.has(url)) cache.set(url, window.MachyonLab.loadRecording(url));
    return cache.get(url);
  }

  function failed(node, err) {
    node.replaceChildren(el("p", { class: "mx-lab-error",
      text: `The recording couldn't be shown: ${err.message}` + (err.problems && err.problems.length ?
        ` (${err.problems.slice(0, 3).join("; ")})` : "") }));
  }

  /** The Lab's palette, read from CSS so light/dark and any future retheme stay in the stylesheet. */
  function palette(node) {
    const cs = getComputedStyle(node);
    const v = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
    return {
      ink: v("--lab-ink", "#352F58"), muted: v("--lab-muted", "#5E557C"), line: v("--lab-line", "#DCD5F2"),
      paper: v("--lab-paper", "#FBFAFF"), signal: v("--lab-signal", "#6A57CC"), signalSoft: v("--lab-signal-soft", "#E6E0FB"),
      ok: v("--lab-ok", "#37733F"), over: v("--lab-over", "#C4412F"), grid: v("--lab-grid", "#ECE8FB"),
      // the night stage keeps the same colours in both schemes
      night: v("--lab-night", "#1B1730"), nightLine: v("--lab-night-line", "#3D3560"), nightText: v("--lab-night-text", "#CFC6EE"),
      nightSignal: v("--lab-night-signal", "#B9A8FF"), nightOk: v("--lab-night-ok", "#8FD3A1"),
      nightOver: v("--lab-night-over", "#FF948A"), nightErr: v("--lab-night-err", "#F2C46A"),
      font: v("--md-text-font-family", "Figtree, system-ui, sans-serif"),
      head: v("--mx-head-font", "Manrope, system-ui, sans-serif"),
    };
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  // ------------------------------------------------------------------ the stage
  /**
   * A canvas window onto the server. The caller sets which slots are busy (and how far along each answer is), and
   * asks for dots when requests leave, images return or requests are turned away; the stage animates the dots.
   *   slots: places inside the server's limit (or, with no limit, the most the run ever had in flight)
   *   door:  places for requests beyond the limit, which the server is turning away (0 when there is no limit)
   */
  function createStage({ slots, door = 0, limitLabel = null, caption = null }) {
    const canvas = el("canvas", { class: "mx-lab-stage-canvas", "aria-hidden": "true" });
    const opa = el("div", { class: "mx-lab-opa", "aria-hidden": "true" },
      el("img", { src: OPA_NIGHT, alt: "", width: 52, height: 52, decoding: "async" }), el("span", { text: "Machyon" }));
    const box = el("div", { class: "mx-lab-stagebox" }, canvas, opa);
    const toggle = el("input", { type: "checkbox", onchange: (e) => setMotion(e.target.checked) });
    const hint = el("span", { class: "mx-lab-motion-hint" });
    const syncToggle = () => {
      toggle.checked = moving();
      hint.textContent = !toggle.checked && reduceMotion() && motionChoice() === null && memo === null
        ? "Your system asks for less motion, so they're still." : "";
    };
    syncToggle();
    window.addEventListener("mx-lab-motion", syncToggle);
    const root = el("figure", { class: "mx-lab-stage" }, box,
      el("figcaption", {}, caption ? el("span", { text: caption }) : null,
        el("span", { class: "mx-lab-motion" }, el("label", {}, toggle, " Moving requests"), hint)));
    let geo = null, busy = [], doorBusy = 0, particles = [], raf = 0, ticking = null, c = null, nowClock = "";
    const DOOR_COLS = 4;

    function layout() {
      const w = Math.max(280, box.clientWidth || 600), h = w < 520 ? 214 : 232, dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); canvas.style.height = `${h}px`;
      const opaW = w < 520 ? 60 : 92, pad = w < 520 ? 8 : 14;
      const cols = slots > 36 ? 8 : 6, rows = Math.ceil(slots / cols);
      const serverW = Math.min(w < 520 ? 0.56 * w : 0.4 * w, cols * 56 + 2 * pad);
      const gap = w < 520 ? 4 : 6;
      const sw = (serverW - 2 * pad - (cols - 1) * gap) / cols;
      const sh = clamp(Math.min(sw * 0.5, (h - 70 - (rows - 1) * gap) / rows), 8, 24);
      const serverH = 40 + rows * sh + (rows - 1) * gap + pad;
      const sx = w - serverW - 12, sy = Math.max(12, (h - serverH) / 2 + 4);
      const slotRects = [];
      for (let i = 0; i < slots; i++) {
        const r = Math.floor(i / cols), k = i % cols;
        slotRects.push([sx + pad + k * (sw + gap), sy + 36 + r * (sh + gap), sw, sh]);
      }
      const doorRows = door ? Math.ceil(door / DOOR_COLS) : 0;
      const dot = clamp(Math.min(sh * 0.75, (h - 64) / Math.max(1, doorRows) - 3), 5, 11), dgap = 3;
      const doorW = DOOR_COLS * dot + (DOOR_COLS - 1) * dgap;
      const doorH = doorRows * dot + Math.max(0, doorRows - 1) * dgap;
      const dx = sx - doorW - (w < 520 ? 14 : 26), dy = sy + 36 + (serverH - 36 - pad - doorH) / 2;
      const doorPts = [];
      for (let j = 0; j < door; j++) {
        doorPts.push([dx + (j % DOOR_COLS) * (dot + dgap) + dot / 2, dy + Math.floor(j / DOOR_COLS) * (dot + dgap) + dot / 2]);
      }
      geo = { w, h, dpr, opaX: opaW, midY: h / 2, sx, sy, serverW, serverH, slotRects, doorPts, dot, dx, dy, doorW, doorH };
    }

    const slotCenter = (i) => { const r = geo.slotRects[i]; return r ? [r[0] + r[2] / 2, r[1] + r[3] / 2] : [geo.sx, geo.midY]; };
    const doorCenter = (j) => geo.doorPts[j] || [geo.dx + geo.doorW / 2, geo.dy + geo.doorH / 2];

    function draw(now) {
      if (!geo) layout();
      if (!c) c = palette(root);
      const ctx = canvas.getContext("2d"), g = geo;
      ctx.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
      ctx.clearRect(0, 0, g.w, g.h);
      // lanes: out along the top, back along the bottom
      ctx.save();
      ctx.strokeStyle = c.nightLine; ctx.lineWidth = 1; ctx.setLineDash([2, 6]);
      for (const dy of [-14, 14]) {
        ctx.beginPath(); ctx.moveTo(g.opaX, g.midY + dy); ctx.lineTo(door ? g.dx - 10 : g.sx - 10, g.midY + dy); ctx.stroke();
      }
      ctx.restore();
      // the dots in flight
      const alive = [];
      for (const p of particles) {
        const k = (now - p.t0) / p.dur;
        if (k >= 1) continue;
        alive.push(p);
        if (k < 0) continue;
        const e = ease(k), x = p.x0 + (p.x1 - p.x0) * e;
        const y = p.y0 + (p.y1 - p.y0) * e - (p.arc || 0) * Math.sin(Math.PI * k);
        ctx.globalAlpha = Math.min(1, k / 0.12, (1 - k) / 0.15);
        ctx.fillStyle = p.color;
        if (p.kind === "img") { roundRect(ctx, x - 3.5, y - 3.5, 7, 7, 2); ctx.fill(); }
        else { ctx.beginPath(); ctx.arc(x, y, p.kind === "turned" ? 3.6 : 3, 0, Math.PI * 2); ctx.fill(); }
        ctx.globalAlpha = 1;
      }
      particles = alive;
      // the server
      roundRect(ctx, g.sx, g.sy, g.serverW, g.serverH, 12);
      ctx.fillStyle = c.night; ctx.fill();
      ctx.fillStyle = "rgba(143, 211, 161, 0.07)"; ctx.fill();
      ctx.strokeStyle = c.nightOk; ctx.globalAlpha = 0.5; ctx.lineWidth = 1.2; ctx.stroke(); ctx.globalAlpha = 1;
      ctx.font = `700 12px ${c.head}`; ctx.fillStyle = c.nightText; ctx.textBaseline = "alphabetic";
      ctx.fillText("Server", g.sx + 12, g.sy + 22);
      if (limitLabel) {
        ctx.font = `600 11px ${c.font}`; ctx.textAlign = "right"; ctx.fillStyle = c.nightOk;
        ctx.fillText(limitLabel, g.sx + g.serverW - 12, g.sy + 22); ctx.textAlign = "left";
      }
      // its slots
      const used = new Array(slots).fill(null);
      for (const b of busy) if (b.slot >= 0 && b.slot < slots) used[b.slot] = b;
      g.slotRects.forEach(([x, y, w, h], i) => {
        const b = used[i];
        roundRect(ctx, x, y, w, h, Math.min(5, h / 2.5));
        if (!b) { ctx.strokeStyle = c.nightLine; ctx.lineWidth = 1; ctx.stroke(); return; }
        const color = b.kind === "err" ? c.nightErr : c.nightSignal;
        ctx.fillStyle = color; ctx.globalAlpha = 0.24; ctx.fill(); ctx.globalAlpha = 1;
        ctx.save(); ctx.clip();
        ctx.fillStyle = color; ctx.fillRect(x, y, w * clamp(b.p, 0.08, 1), h);
        ctx.restore();
      });
      // beyond the limit
      if (door) {
        ctx.save(); ctx.setLineDash([3, 4]); ctx.strokeStyle = c.nightOver; ctx.globalAlpha = doorBusy ? 0.8 : 0.35;
        roundRect(ctx, g.dx - 7, g.dy - 7, g.doorW + 14, g.doorH + 14, 8); ctx.stroke(); ctx.restore();
        ctx.font = `600 10.5px ${c.font}`; ctx.fillStyle = c.nightOver; ctx.globalAlpha = doorBusy ? 1 : 0.6;
        ctx.textAlign = "center";
        ctx.fillText("over the limit", g.dx + g.doorW / 2, g.dy + g.doorH + 22); ctx.textAlign = "left"; ctx.globalAlpha = 1;
        g.doorPts.forEach(([x, y], j) => {
          ctx.beginPath(); ctx.arc(x, y, g.dot / 2, 0, Math.PI * 2);
          if (j < doorBusy) { ctx.fillStyle = c.nightOver; ctx.fill(); }
          else { ctx.globalAlpha = 0.5; ctx.strokeStyle = c.nightLine; ctx.lineWidth = 1; ctx.stroke(); ctx.globalAlpha = 1; }
        });
      }
      if (nowClock) {
        ctx.font = `700 12px ${c.head}`; ctx.fillStyle = c.nightText; ctx.textAlign = "right";
        ctx.fillText(nowClock, g.w - 14, g.h - 10); ctx.textAlign = "left";
      }
    }

    function loop(now) {
      raf = -1;                       // "running": a kick() from inside this frame must not schedule a second loop
      if (!root.isConnected) { raf = 0; return; }
      if (ticking) ticking(now);
      draw(now);
      raf = ticking || particles.length ? requestAnimationFrame(loop) : 0;
    }
    const kick = () => { if (!raf) raf = requestAnimationFrame(loop); };

    function spawn(kind, at, delay = 0) {
      if (!moving() || !geo) return;
      if (particles.length > 900) return;   // thinned at high speed; the counters carry the exact numbers
      if (!c) c = palette(root);
      const now = performance.now() + delay, j = (Math.random() - 0.5) * 9, o = geo.opaX + 2;
      if (kind === "req") {           // leaves Machyon for a slot
        const [x, y] = slotCenter(at);
        particles.push({ kind, x0: o, y0: geo.midY - 14 + j, x1: x, y1: y, t0: now, dur: 460, color: c.nightSignal });
      } else if (kind === "reqOver") { // leaves Machyon and lands beyond the limit
        const [x, y] = doorCenter(at);
        particles.push({ kind: "req", x0: o, y0: geo.midY - 14 + j, x1: x, y1: y, t0: now, dur: 420, color: c.nightSignal });
      }
      kick();
    }

    /** An answer travelling back to Machyon. Where it starts is where the request was drawn (a slot, or beyond the
        limit); what it looks like is decided only by the answer itself (see answerFor). */
    function answer(from, at, kind, delay = 0) {
      if (!moving() || !geo) return;
      if (particles.length > 900) return;
      if (!c) c = palette(root);
      const now = performance.now() + delay, j = (Math.random() - 0.5) * 9, o = geo.opaX + 2;
      const [x, y] = from === "door" ? doorCenter(at) : slotCenter(at);
      const look = {
        ok: { kind: "img", color: c.nightOk, dur: 520 },                  // an image: a green square
        throttled: { kind: "turned", color: c.nightOver, dur: 560, arc: 18 },   // a 429: a coral dot that bounces
        error: { kind: "err", color: c.nightErr, dur: 520 },              // anything else: a honey dot
      }[kind];
      particles.push({ kind: look.kind, x0: x, y0: y, x1: o, y1: geo.midY + 14 + j, t0: now, dur: look.dur,
        arc: look.arc || 0, color: look.color });
      kick();
    }

    const ro = new ResizeObserver(() => {
      if (!root.isConnected) { ro.disconnect(); return; }
      geo = null; layout(); draw(performance.now());
    });
    ro.observe(box);
    const mo = new MutationObserver(() => { c = null; draw(performance.now()); });
    mo.observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });

    return {
      el: root,
      /** busy: [{slot, p (0..1 through its answer), kind: "ok" | "err"}]; overs: requests beyond the limit now. */
      set(list, overs = 0, clock = "") { busy = list; doorBusy = Math.min(door, overs); nowClock = clock; kick(); },
      spawn, answer,
      /** fn(now) runs every frame until stopped; used by the model's own clock. */
      animate(fn) { ticking = fn; kick(); },
      stop() { ticking = null; },
      draw: () => draw(performance.now()),
    };
  }

  /** What a finished request brought back, from the server's own record of it, and nothing else: a complete 200 is an
      image, a 429 is "too many", anything else (a 503, a cut-off body, no status at all) is an error. */
  function answerKind(status, outcome) {
    if (status === 200 && (outcome === undefined || outcome === null || outcome === "complete")) return "ok";
    return status === 429 ? "throttled" : "error";
  }
  /** The returning dot for one request: it leaves from where the request was drawn, and looks like its answer. A
      request can arrive while the server counts more than its limit and still be served (the server decides after a
      new connection's handshake, when others may have finished), so the place must never decide the colour. */
  function answerFor(r) {
    return { from: r.over ? "door" : "slot", kind: answerKind(r.status, r.outcome) };
  }

  /** Requests as the server saw them, paired start to end by request id, with a stable place on the stage for each.
      A request is drawn beyond the limit when the server counted more than `cap` in flight as it arrived. */
  function placeRequests(events, cap) {
    const open = new Map(), reqs = [];
    const freeSlots = [], freeDoor = [];
    let nextSlot = 0, nextDoor = 0;
    const take = (pool, next) => (pool.length ? pool.sort((a, b) => b - a).pop() : next);
    for (const e of events) {
      if (e.event === "start") {
        const over = cap !== null && e.inflight > cap;
        let place;
        if (over) { place = take(freeDoor, nextDoor); if (place === nextDoor) nextDoor++; }
        else { place = take(freeSlots, nextSlot); if (place === nextSlot) nextSlot++; }
        const r = { id: e.id, s: e.t_ms, e: Infinity, status: null, outcome: null, over, place };
        open.set(e.id, r); reqs.push(r);
      } else {
        const r = open.get(e.id);
        if (!r) continue;
        open.delete(e.id);
        r.e = e.t_ms; r.status = e.status; r.outcome = e.outcome;
        (r.over ? freeDoor : freeSlots).push(r.place);
      }
    }
    return reqs;
  }

  // ------------------------------------------------------------------ instruments (measured at the server)
  function createInstruments({ cap, scaleMax, total }) {
    const meter = (...fills) => el("span", { class: "mx-lab-meter", "aria-hidden": "true" }, ...fills);
    const okFill = el("i", { class: "ok" }), overFill = el("i", { class: "over" });
    const tick = cap ? el("i", { class: "tick", style: `left:${(cap / scaleMax * 100).toFixed(2)}%` }) : null;
    const delFill = el("i", { class: "ok" });
    const v = { inflight: el("b"), delivered: el("b"), throttled: el("b"), recent: el("b") };
    const card = (key, label, extra, ...more) => el("div", { class: `mx-lab-inst ${key}` },
      el("div", { class: "mx-lab-inst-num" }, v[key], extra), el("span", { class: "mx-lab-inst-label", text: label }), ...more);
    const root = el("section", { class: "mx-lab-instruments" },
      el("h4", { text: "Measured at the server" }),
      el("div", { class: "mx-lab-inst-row" },
        card("inflight", "requests in flight now", cap ? el("small", { text: `/ ${cap} limit` }) : null, meter(okFill, overFill, tick)),
        card("delivered", "images delivered so far", total ? el("small", { text: `of ${num(total)}` }) : null, meter(delFill)),
        card("throttled", "turned away (429) so far", null),
        card("recent", "turned away in the last ½ second", el("span", { class: "mx-lab-pulse", "aria-hidden": "true" }))));
    return {
      el: root, values: v,
      set({ inflight, delivered, throttled, recent }) {
        v.inflight.textContent = num(inflight);
        v.delivered.textContent = num(delivered);
        v.throttled.textContent = num(throttled);
        v.recent.textContent = num(recent);
        const inLimit = cap ? Math.min(inflight, cap) : inflight;
        okFill.style.width = `${(inLimit / scaleMax * 100).toFixed(2)}%`;
        overFill.style.left = okFill.style.width;
        overFill.style.width = `${(Math.max(0, inflight - inLimit) / scaleMax * 100).toFixed(2)}%`;
        delFill.style.width = total ? `${(delivered / total * 100).toFixed(2)}%` : "0";
        v.inflight.closest(".mx-lab-inst").classList.toggle("over", Boolean(cap && inflight > cap));
        v.throttled.closest(".mx-lab-inst").classList.toggle("over", throttled > 0);
        v.recent.closest(".mx-lab-inst").classList.toggle("over", recent > 0);
      },
    };
  }

  // ------------------------------------------------------------------ 1. the model
  function mountModel(node) {
    const CAP = 24, RTT_MS = 100, MAX = 64, ROUND = 1100;   // one 100 ms round, slowed to ~1.1 s so it can be followed
    const slider = el("input", { type: "range", min: 1, max: MAX, value: 8, id: "mx-lab-model-n",
      class: "mx-lab-slider", "aria-describedby": "mx-lab-model-out" });
    const value = el("output", { for: "mx-lab-model-n", class: "mx-lab-slider-value" });
    const stage = createStage({ slots: CAP, door: MAX - CAP, limitLabel: `limit ${CAP}`,
      caption: "Model, slowed down so you can follow it: each 100 ms round takes about a second here." });
    const out = el("div", { class: "mx-lab-readout", id: "mx-lab-model-out", "aria-live": "polite" });
    // throughput curve: min(n, CAP) per RTT
    const W = 320, H = 132, P = 28;
    const x = (n) => P + (n - 1) / (MAX - 1) * (W - 2 * P);
    const y = (rate) => H - P - rate / (CAP * 1000 / RTT_MS) * (H - 2 * P - 6);
    const pts = [...Array(MAX).keys()].map((k) => [x(k + 1), y(Math.min(k + 1, CAP) * 1000 / RTT_MS)]);
    const d = pts.map(([px, py], k) => `${k ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
    const dot = el("svg:circle", { r: 6, class: "mx-lab-dot" });
    const curve = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, class: "mx-lab-curve", role: "img",
      "aria-label": "Model: images per second rise with requests in flight until 24, then stay flat" },
      el("svg:rect", { x: x(CAP), y: P - 10, width: W - P - x(CAP), height: H - 2 * P + 10, class: "mx-lab-curve-over" }),
      el("svg:path", { d: `${pts.slice(0, CAP).map(([px, py], k) => `${k ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("")}` +
        `L${x(CAP).toFixed(1)},${(H - P).toFixed(1)}L${P},${(H - P).toFixed(1)}Z`, class: "mx-lab-curve-area" }),
      el("svg:line", { x1: x(CAP), x2: x(CAP), y1: P - 10, y2: H - P, class: "mx-lab-limit" }),
      el("svg:text", { x: x(CAP) + 6, y: P - 1, class: "mx-lab-svgtext over", text: "limit 24" }),
      el("svg:path", { d, class: "mx-lab-curve-line" }),
      el("svg:line", { x1: P, x2: W - P, y1: H - P, y2: H - P, class: "mx-lab-axis" }),
      el("svg:text", { x: W - P, y: H - 8, "text-anchor": "end", class: "mx-lab-svgtext", text: "requests in flight" }),
      el("svg:text", { x: P, y: P - 14, class: "mx-lab-svgtext", text: "images per second" }),
      dot);
    let n = 8, served = 8, turned = 0;
    const lastP = new Float64Array(MAX);
    const phase = (i, count) => (count ? i / count : 0);
    function frame(now) {
      const busy = [];
      for (let i = 0; i < served; i++) {
        const p = (now / ROUND + phase(i, served)) % 1;
        if (p < lastP[i]) { stage.answer("slot", i, "ok"); stage.spawn("req", i, 40); }
        lastP[i] = p;
        busy.push({ slot: i, p, kind: "ok" });
      }
      for (let k = 0; k < turned; k++) {
        const p = (now / ROUND + phase(k, turned) + 0.37) % 1, j = CAP + k;
        if (p < lastP[j]) { stage.spawn("reqOver", k); stage.answer("door", k, "throttled", 380); }
        lastP[j] = p;
      }
      stage.set(busy, turned);
    }
    function still() {   // reduced motion or off screen: the same picture, frozen
      stage.set([...Array(served).keys()].map((i) => ({ slot: i, p: 0.15 + 0.8 * phase(i, served), kind: "ok" })), turned);
    }
    function update() {
      n = Number(slider.value);
      served = Math.min(n, CAP); turned = Math.max(0, n - CAP);
      value.textContent = num(n);
      value.classList.toggle("over", turned > 0);
      dot.setAttribute("cx", x(n).toFixed(1));
      dot.setAttribute("cy", y(served * 1000 / RTT_MS).toFixed(1));
      dot.classList.toggle("over", turned > 0);
      out.replaceChildren(
        el("div", { class: "mx-lab-big ok" }, el("b", { text: num(served * 1000 / RTT_MS) }), el("span", { text: "images per second" })),
        el("div", { class: `mx-lab-big${turned ? " over" : ""}` }, el("b", { text: num(turned) }),
          el("span", { text: turned === 1 ? "request turned away (429) every round" : "requests turned away (429) every round" })));
      if (!moving() || !visible) still();
    }
    slider.addEventListener("input", update);
    let visible = false;
    const io = new IntersectionObserver(([entry]) => {
      if (!node.isConnected) { io.disconnect(); stage.stop(); return; }
      visible = entry.isIntersecting;
      run();
    });
    function run() { if (visible && moving()) stage.animate(frame); else { stage.stop(); still(); } }
    window.addEventListener("mx-lab-motion", () => { if (node.isConnected) run(); });
    node.replaceChildren(
      el("div", { class: "mx-lab-slider-row" },
        el("label", { for: "mx-lab-model-n", class: "mx-lab-label" }, "Requests in flight"), value),
      // green up to the limit, coral past it; positions follow the thumb's centre, not the track's edge
      el("div", { class: "mx-lab-slider-wrap",
        style: `--capf:${((CAP - 0.5) / (MAX - 1)).toFixed(4)};--tickf:${((CAP - 1) / (MAX - 1)).toFixed(4)}` }, slider,
        el("span", { class: "mx-lab-slider-tick", "aria-hidden": "true", text: "limit 24" })),
      stage.el,
      el("div", { class: "mx-lab-model-body" }, out, curve));
    update();
    io.observe(node);
  }

  // ------------------------------------------------------------------ 2. the prediction
  const CHOICES = [
    ["same", "It keeps the same number in flight: the 429s get retried anyway."],
    ["more", "It sends more, to make up for the time lost."],
    ["now", "It cuts back straight away, within the same second."],
    ["later", "It cuts back, but a second or two later."],
  ];

  async function mountPredict(node) {
    let rec;
    try { rec = await recording(node); } catch (e) { return failed(node, e); }
    const run = rec.runs.find((r) => r.name === "adaptive");
    const reaction = run && run.analysis.reaction_to_429;
    if (!reaction) {
      node.replaceChildren(el("p", { text: "In this recording the adaptive run never met the server's limit, so " +
        "there is nothing to predict. The replay below shows what it did instead." }));
      return;
    }
    const before = reaction.inflight_peak_before;
    const after = reaction.inflight_peak_by_second_after;
    // A cut is a real drop (at least a fifth below the peak), not a request or two of noise.
    const firstCut = after.findIndex((v) => v !== null && v <= 0.8 * before);
    const answer = firstCut === -1 ? (after.some((v) => v !== null && v > before) ? "more" : "same")
      : firstCut === 0 ? "now" : "later";
    const why = {
      later: "Machyon measures its speed over one-second windows and changes its limit only when a window ends, " +
        "so the cut comes a second or two after the first 429. Requests already on their way also have to finish.",
      now: "In this recording the cut came within the first second.",
      same: "In this recording Machyon kept the same number in flight.",
      more: "In this recording Machyon sent more.",
    }[answer];
    const result = el("div", { class: "mx-lab-answer", "aria-live": "polite" });
    const group = el("div", { class: "mx-lab-choices", role: "group",
      "aria-label": "What happens to the number of requests in flight after the first 429?" });
    const buttons = CHOICES.map(([key, text], i) => el("button", { type: "button", class: "mx-lab-choice",
      "data-key": key, onclick: () => choose(key) }, el("span", { class: "mx-lab-choice-key", "aria-hidden": "true",
        text: "ABCD"[i] }), el("span", { text })));
    group.append(...buttons);

    // the peaks around the first 429, drawn as bars: the answer as a picture
    function bars() {
      const vals = [before, ...after], max = Math.max(...vals.filter((v) => v !== null), 1);
      const ceiling = rec.notes && rec.notes.server_ceiling;
      return el("div", { class: "mx-lab-answer-bars", role: "img", "aria-label": `Most in flight: ${before} before the first ` +
        `429, then ${after.map((v) => num(v)).join(", ")} in each of the next five seconds` },
        // coral where the server was over its limit; the first real cut is marked
        vals.map((v, i) => el("div", { class: `bar${ceiling && v > ceiling ? " over" : ""}${i - 1 === firstCut ? " cut" : ""}` },
          el("span", { class: "fill", style: `height:${((v || 0) / max * 100).toFixed(1)}%` },
            el("b", { text: num(v) })),
          el("small", { text: i === 0 ? "before" : `${i - 1}–${i} s` }))),
        ceiling ? el("span", { class: "limit", style: `bottom:${(ceiling / max * 100).toFixed(1)}%` },
          el("small", { text: `limit ${ceiling}` })) : null);
    }

    function choose(key) {
      buttons.forEach((b) => {
        b.disabled = true;
        b.classList.toggle("chosen", b.dataset.key === key);
        b.classList.toggle("correct", b.dataset.key === answer);
      });
      const right = key === answer;
      const watch = el("button", { type: "button", class: "mx-lab-btn", text: "Watch it in the replay",
        onclick: () => {
          const c = controllers.get(urlOf(node));
          if (!c) return;
          c.select("adaptive");
          c.seek(Math.max(0, reaction.first_429_ms - 2500));
          c.node.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
          c.play();
        } });
      result.replaceChildren(
        el("p", { class: right ? "yes" : "no", text: right ? "That's what happened." : "Not quite. Here's what happened." }),
        el("p", { text: `The server sent its first 429 at ${secs(reaction.first_429_ms)}, with ${before} requests ` +
          `in flight. The most in flight in each of the next five seconds: ${after.map((v) => num(v)).join(", ")}.` }),
        bars(),
        el("p", { text: why }),
        watch);
      watch.focus({ preventScroll: true });   // the chosen button is now disabled: keep keyboard users in place
    }
    node.replaceChildren(
      el("p", { class: "mx-lab-q", text: "The server starts answering 429, \"too many requests\". What happens to " +
        "the number of requests Machyon keeps in flight?" }),
      group, result);
  }

  // ------------------------------------------------------------------ 3. the replay
  /** Per-run lookups built once from the decoded server events. */
  function indexRun(run, cap) {
    const evs = window.MachyonLab.eventsOf(run);
    const t = new Float64Array(evs.length), inflight = new Int32Array(evs.length);
    const ok = [], throttled = [];
    evs.forEach((e, i) => {
      t[i] = e.t_ms; inflight[i] = e.inflight;
      if (e.event === "end") {
        if (e.status === 200 && e.outcome === "complete") ok.push(e.t_ms);
        else if (e.status === 429) throttled.push(e.t_ms);
      }
    });
    const reqs = placeRequests(evs, cap);
    const starts = reqs.slice();                                    // already in start order
    const ends = reqs.filter((r) => r.e !== Infinity).sort((a, b) => a.e - b.e);
    return { run, t, inflight, ok, throttled, reqs, starts, ends, endT: Float64Array.from(ends, (r) => r.e),
      startT: Float64Array.from(starts, (r) => r.s), span: run.analysis.span_ms };
  }
  /** Number of values in sorted `arr` that are <= x. */
  function countTo(arr, x) {
    let lo = 0, hi = arr.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] <= x) lo = mid + 1; else hi = mid; }
    return lo;
  }
  function inflightAt(ix, ms) {
    const k = countTo(ix.t, ms);
    return k === 0 ? 0 : ix.inflight[k - 1];
  }

  function sparkline(run, maxSpan, maxPeak, cap) {
    const s = run.analysis.series, W = 120, H = 30;
    const x = (ms) => 1 + ms / maxSpan * (W - 2), y = (v) => H - 2 - v / maxPeak * (H - 5);
    const step = Math.max(1, Math.floor(s.bins / 90));
    let d = "";
    for (let b = 0; b < s.bins; b += step) {
      const v = Math.max(...s.inflight_max.slice(b, b + step));
      d += `${b ? "L" : "M"}${x(b * s.bin_ms).toFixed(1)},${y(v).toFixed(1)}L${x(Math.min(s.bins, b + step) * s.bin_ms).toFixed(1)},${y(v).toFixed(1)}`;
    }
    return el("svg:svg", { viewBox: `0 0 ${W} ${H}`, class: "mx-lab-spark", "aria-hidden": "true" },
      cap ? el("svg:line", { x1: 0, x2: W, y1: y(cap), y2: y(cap), class: "cap" }) : null,
      el("svg:path", { d }));
  }

  async function mountReplay(node) {
    let rec;
    try { rec = await recording(node); } catch (e) { return failed(node, e); }
    const CAP = (rec.notes && rec.notes.server_ceiling) || null;
    const runs = rec.runs.map((r) => indexRun(r, CAP));
    const maxSpan = Math.max(...runs.map((r) => r.span));
    const maxPeak = Math.max(...runs.map((r) => r.run.analysis.server_inflight_peak), CAP || 0);
    const slots = CAP || maxPeak, door = CAP ? Math.max(0, maxPeak - CAP) : 0;
    const total = rec.input && rec.input.images;
    let cur = runs.find((r) => r.run.name === "adaptive") || runs[0];
    let t = 0, prevT = 0, playing = false, last = 0, speed = 1, raf = 0, showOthers = false;

    const stage = createStage({ slots, door, limitLabel: CAP ? `limit ${CAP}` : null });
    const instruments = createInstruments({ cap: CAP, scaleMax: Math.max(maxPeak, CAP || 0), total });

    // run picker: the shape of each run at a glance
    const tabs = el("div", { class: "mx-lab-tabs", role: "group", "aria-label": "Run" },
      runs.map((r) => el("button", { type: "button", class: "mx-lab-tab", "data-run": r.run.name,
        onclick: () => select(r.run.name) }, sparkline(r.run, maxSpan, maxPeak * 1.05, CAP), el("span", { text: r.run.label }))));

    // transport
    const icon = (d) => el("svg:svg", { viewBox: "0 0 16 16", "aria-hidden": "true", class: "mx-lab-icon" }, el("svg:path", { d }));
    const PLAY = "M4 2.5v11l9-5.5z", PAUSE = "M4 2.5h3v11H4zM9 2.5h3v11H9z";
    const playIcon = icon(PLAY), playText = el("span", { text: "Play" });
    const playBtn = el("button", { type: "button", class: "mx-lab-btn mx-lab-play", onclick: () => (playing ? pause() : play()) },
      playIcon, playText);
    const restartBtn = el("button", { type: "button", class: "mx-lab-btn quiet", text: "Restart",
      onclick: () => { seek(0); play(); } });
    const speedSel = el("select", { "aria-label": "Playback speed", class: "mx-lab-speed",
      onchange: (e) => { speed = Number(e.target.value); } },
      [0.5, 1, 2, 4, 10].map((s) => el("option", { value: s, selected: s === speed, text: `${s}×` })));
    const scrub = el("input", { type: "range", min: 0, max: 1000, value: 0, class: "mx-lab-scrub",
      "aria-label": "Position in the run", oninput: (e) => { pause(); seek(e.target.value / 1000 * cur.span); } });
    const clock = el("span", { class: "mx-lab-clock" });
    const others = el("label", { class: "mx-lab-toggle" },
      el("input", { type: "checkbox", onchange: (e) => { showOthers = e.target.checked; drawBase(); render(); } }),
      " Show the other runs, faintly");

    // what Machyon reported
    const opaMark = el("span", { class: "mx-lab-receipt-opa", "aria-hidden": "true" },
      el("img", { src: OPA_LIGHT, alt: "", width: 34, height: 34, class: "mx-only-light" }),
      el("img", { src: OPA_NIGHT, alt: "", width: 34, height: 34, class: "mx-only-dark" }));
    const receiptBody = el("div", { class: "mx-lab-receipt-body" });
    const receipt = el("section", { class: "mx-lab-receipt", "aria-live": "polite" },
      el("header", {}, opaMark, el("h4", { text: "Reported by Machyon" })), receiptBody);
    const status = el("p", { class: "mx-lab-sr", "aria-live": "polite" });

    // the whole run as a chart, which is also a timeline you can drag
    const canvas = el("canvas", { class: "mx-lab-chart", role: "img" });
    const tableBox = el("details", { class: "mx-lab-table" });
    let base = null;

    node.replaceChildren(
      tabs, stage.el, instruments.el,
      el("div", { class: "mx-lab-controls" }, playBtn, restartBtn, speedSel, scrub, clock),
      canvas, others, receipt, tableBox, status);

    // ---- drawing the chart
    const PAD = { l: 34, r: 14, t: 18, b: 46 };
    function geometry() {
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(280, canvas.clientWidth || node.clientWidth || 600), h = w < 520 ? 210 : 250;
      return { dpr, w, h, x: (ms) => PAD.l + ms / maxSpan * (w - PAD.l - PAD.r),
        y: (n) => PAD.t + (1 - n / (maxPeak * 1.12)) * (h - PAD.t - PAD.b - 22) };
    }
    function drawBase() {
      const g = geometry(), c = palette(node);
      const off = document.createElement("canvas");
      off.width = Math.round(g.w * g.dpr); off.height = Math.round(g.h * g.dpr);
      const ctx = off.getContext("2d");
      ctx.scale(g.dpr, g.dpr);
      ctx.font = `11px ${c.font}`;
      const bottom = g.h - PAD.b;
      // gridlines and labels
      ctx.strokeStyle = c.grid; ctx.lineWidth = 1; ctx.fillStyle = c.muted;
      const step = maxPeak > 40 ? 16 : 8;
      for (let n = 0; n <= maxPeak * 1.12; n += step) {
        ctx.beginPath(); ctx.moveTo(PAD.l, g.y(n)); ctx.lineTo(g.w - PAD.r, g.y(n)); ctx.stroke();
        ctx.fillText(String(n), 6, g.y(n) + 4);
      }
      for (let s = 0; s * 1000 <= maxSpan; s += maxSpan > 30000 ? 10 : 5) {
        ctx.fillText(`${s} s`, g.x(s * 1000) - 6, g.h - 6);
      }
      const series = (r) => r.run.analysis.series;
      const path = (r) => {
        const s = series(r);
        ctx.beginPath();
        s.inflight_max.forEach((v, b) => {
          const x0 = g.x(b * s.bin_ms), x1 = g.x(Math.min((b + 1) * s.bin_ms, r.span));
          if (b === 0) ctx.moveTo(x0, g.y(v)); else ctx.lineTo(x0, g.y(v));
          ctx.lineTo(x1, g.y(v));
        });
      };
      if (showOthers) for (const r of runs) if (r !== cur) {
        ctx.save(); ctx.globalAlpha = 0.28; ctx.strokeStyle = c.muted; ctx.lineWidth = 1; path(r); ctx.stroke(); ctx.restore();
      }
      // the chosen run: a soft area with a strong edge
      const s = series(cur), zero = g.y(0);
      path(cur);
      ctx.save();
      ctx.lineTo(g.x(cur.span), zero); ctx.lineTo(g.x(0), zero); ctx.closePath();
      const grad = ctx.createLinearGradient(0, g.y(maxPeak), 0, zero);
      grad.addColorStop(0, c.signal); grad.addColorStop(1, "transparent");
      ctx.globalAlpha = 0.22; ctx.fillStyle = grad; ctx.fill(); ctx.restore();
      path(cur);
      ctx.strokeStyle = c.signal; ctx.lineWidth = 2.2; ctx.lineJoin = "round"; ctx.stroke();
      // the server's limit
      if (CAP) {
        ctx.save(); ctx.setLineDash([5, 4]); ctx.strokeStyle = c.over; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(PAD.l, g.y(CAP)); ctx.lineTo(g.w - PAD.r, g.y(CAP)); ctx.stroke(); ctx.restore();
        const label = `server limit ${CAP}`;
        ctx.font = `700 11px ${c.font}`;
        const tw = ctx.measureText(label).width, lx = g.w - PAD.r - tw - 12, ly = g.y(CAP) - 20;
        roundRect(ctx, lx, ly, tw + 12, 16, 8); ctx.fillStyle = c.paper; ctx.fill();
        ctx.fillStyle = c.over; ctx.fillText(label, lx + 6, ly + 12);
        ctx.font = `11px ${c.font}`;
      }
      // 429s per bin, as a strip under the chart
      const stripTop = bottom - 16, maxT = Math.max(1, ...s.throttled_429);
      ctx.fillStyle = c.over;
      s.throttled_429.forEach((v, b) => {
        if (!v) return;
        const hgt = Math.max(2, 14 * v / maxT);
        ctx.fillRect(g.x(b * s.bin_ms), stripTop + 14 - hgt, Math.max(1.5, g.x(s.bin_ms) - g.x(0) - 0.5), hgt);
      });
      ctx.fillText("429s", g.w - PAD.r - 26, stripTop + 2);
      // where the first 429 came, from the analysis
      const first = cur.run.analysis.first_429_ms;
      if (first !== null && first !== undefined) {
        const fx = g.x(first);
        ctx.save(); ctx.strokeStyle = c.over; ctx.globalAlpha = 0.5; ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(fx, PAD.t); ctx.lineTo(fx, stripTop - 2); ctx.stroke(); ctx.restore();
        ctx.font = `700 11px ${c.font}`; ctx.fillStyle = c.over;
        ctx.textAlign = fx > g.w - 120 ? "right" : "left";
        ctx.fillText("first 429", fx + (ctx.textAlign === "right" ? -5 : 5), PAD.t + 4);
        ctx.textAlign = "left";
      }
      base = { g, img: off };
      canvas.width = off.width; canvas.height = off.height;
      canvas.style.height = `${g.h}px`;
      canvas.setAttribute("aria-label", `Requests in flight at the server over time, ${cur.run.label}` +
        (CAP ? `, with the server's limit of ${CAP}` : "") + ". Red marks underneath are 429 responses. " +
        "Drag across the chart to move through the run.");
    }

    let reportShown = null;
    function render() {
      if (!base) return;
      const ctx = canvas.getContext("2d"), g = base.g, c = palette(node);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);  // the base is transparent: clear, or old playheads pile up
      ctx.drawImage(base.img, 0, 0);
      ctx.scale(g.dpr, g.dpr);
      const n = inflightAt(cur, t);
      ctx.strokeStyle = c.ink; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(g.x(t), PAD.t - 6); ctx.lineTo(g.x(t), g.h - PAD.b); ctx.stroke();
      ctx.fillStyle = CAP && n > CAP ? c.over : c.signal; ctx.strokeStyle = c.paper; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(g.x(t), g.y(n), 5.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      // the stage: who is in a slot right now, and how far along their answer is
      const busy = [];
      let overs = 0;
      for (const r of cur.reqs) {
        if (r.s > t) break;
        if (r.e <= t) continue;
        if (r.over) { overs++; continue; }
        busy.push({ slot: r.place, p: r.e === Infinity ? 0.5 : (t - r.s) / Math.max(1, r.e - r.s),
          kind: r.status === null || r.status === 200 || r.status === 429 ? "ok" : "err" });
      }
      stage.set(busy, overs, `${secs(t)}`);
      if (playing && t > prevT && t - prevT < 1500) {      // dots for what the playhead just crossed
        // The server answers in rounds, so one frame can hold a whole burst. When there are more events than dots,
        // sample evenly across the burst: taking the first few would always drop the latecomers, which are exactly
        // the requests over the limit.
        const PER_FRAME = 36;
        const sample = (from, to, fn) => {
          const n = to - from, step = Math.max(1, n / PER_FRAME);
          for (let i = 0; i < n && i / step < PER_FRAME; i += step) fn(from + Math.floor(i));
        };
        const late = (ms) => -(t - ms) / speed;     // each dot starts when its event happened, not at this frame
        sample(countTo(cur.startT, prevT), countTo(cur.startT, t), (k) => {
          const r = cur.starts[k];
          stage.spawn(r.over ? "reqOver" : "req", r.place, late(r.s));
        });
        sample(countTo(cur.endT, prevT), countTo(cur.endT, t), (k) => {
          const r = cur.ends[k];
          const a = answerFor(r);
          stage.answer(a.from, r.place, a.kind, late(r.e));
        });
      }
      prevT = t;
      // readouts
      const throttledSoFar = countTo(cur.throttled, t);
      instruments.set({ inflight: n, delivered: countTo(cur.ok, t), throttled: throttledSoFar,
        recent: throttledSoFar - countTo(cur.throttled, t - 500) });
      clock.textContent = `${secs(t)} / ${secs(cur.span)}`;
      scrub.value = String(Math.round(t / cur.span * 1000));
      machyonReport(t >= cur.span);
    }

    function countUp(b, value, fmt) {
      if (!moving()) { b.textContent = fmt(value); return; }
      const t0 = performance.now(), dur = 700;
      const step = (now) => {
        const k = Math.min(1, (now - t0) / dur);
        b.textContent = fmt(value * (1 - (1 - k) ** 3));
        if (k < 1 && b.isConnected) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
    function story(run) {
      const rep = run.machyon.report, cfg = run.machyon.config || {};
      const start = cfg.concurrency, peak = rep.peak_concurrency, fin = rep.final_concurrency;
      let s = cfg.adaptive ? `It started with ${num(start)} requests in flight, rose to ${num(peak)} and finished at ${num(fin)}.`
        : peak === fin ? `It kept ${num(peak)} requests in flight from start to finish.`
          : `It peaked at ${num(peak)} requests in flight and finished at ${num(fin)}.`;
      s += rep.throttled ? ` The server said "too many" ${num(rep.throttled)} times, and Machyon retried ${num(rep.retries)} times.`
        : " The server never had to say \"too many\".";
      s += rep.downloaded + rep.skipped === rep.total ? " Every image arrived."
        : ` ${num(rep.failed)} ${rep.failed === 1 ? "image" : "images"} couldn't be downloaded.`;
      return s;
    }
    function machyonReport(done) {
      const key = `${cur.run.name}:${done}`;
      if (reportShown === key) return;
      reportShown = key;
      receipt.classList.toggle("done", done);
      if (!done) {
        receiptBody.replaceChildren(el("p", { class: "mx-lab-wait",
          text: "Machyon reports once, when the run finishes. Play to the end to see it." }),
        el("button", { type: "button", class: "mx-lab-btn quiet", text: "Skip to the end",
          onclick: () => { pause(); seek(cur.span); } }));
        return;
      }
      const rep = cur.run.machyon.report;
      const downloaded = el("b"), took = el("b");
      const fact = (value, label) => el("div", {}, el("dt", { text: label }), el("dd", { text: value }));
      receiptBody.replaceChildren(
        el("div", { class: "mx-lab-receipt-head" },
          el("div", {}, el("div", { class: "mx-lab-receipt-num" }, downloaded, el("small", { text: ` / ${num(rep.total)}` })),
            el("span", { text: "downloaded" })),
          el("div", {}, el("div", { class: "mx-lab-receipt-num" }, took, el("small", { text: " s" })),
            el("span", { text: "total time" }))),
        el("p", { class: "mx-lab-story", text: story(cur.run) }),
        el("dl", { class: "mx-lab-facts" },
          fact(num(rep.throttled), "throttled responses"),
          fact(num(rep.retries), "retries"),
          fact(`${num(rep.peak_concurrency)} → ${num(rep.final_concurrency)}`, "concurrency, peak → final"),
          fact(`${num(rep.latency_p50 * 1000)} / ${num(rep.latency_p95 * 1000)} ms`, "latency p50 / p95")));
      countUp(downloaded, rep.downloaded, (v) => num(Math.round(v)));
      countUp(took, rep.duration, (v) => num(v, 1));
    }
    function table() {
      const s = cur.run.analysis.series, perSec = Math.max(1, Math.round(1000 / s.bin_ms));
      const rows = [];
      for (let b = 0; b < s.bins; b += perSec) {
        const slice = (arr) => arr.slice(b, b + perSec);
        rows.push(el("tr", {}, el("td", { text: `${num(b * s.bin_ms / 1000)}–${num((b + perSec) * s.bin_ms / 1000)} s` }),
          el("td", { text: num(Math.max(...slice(s.inflight_max))) }),
          el("td", { text: num(slice(s.completed_200).reduce((a, v) => a + v, 0)) }),
          el("td", { text: num(slice(s.throttled_429).reduce((a, v) => a + v, 0)) })));
      }
      tableBox.replaceChildren(el("summary", { text: `The data as a table: ${cur.run.label}, second by second` }),
        el("table", {}, el("thead", {}, el("tr", {}, ["Time", "Most in flight", "Images delivered", "429s"]
          .map((x) => el("th", { text: x })))), el("tbody", {}, rows)));
    }

    // ---- playback
    function frame(now) {
      raf = 0;
      if (!playing) return;
      t = Math.min(cur.span, t + (now - last) * speed);
      last = now;
      render();
      if (t >= cur.span) { pause(); return; }
      raf = requestAnimationFrame(frame);
    }
    function play() {
      if (t >= cur.span) { t = 0; prevT = 0; }
      playing = true; playText.textContent = "Pause"; playIcon.firstChild.setAttribute("d", PAUSE);
      node.classList.add("playing");
      last = performance.now(); prevT = t;
      if (!raf) raf = requestAnimationFrame(frame);
    }
    function pause() {
      playing = false; playText.textContent = "Play"; playIcon.firstChild.setAttribute("d", PLAY);
      node.classList.remove("playing");
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      announce();
    }
    function seek(ms) { t = clamp(ms, 0, cur.span); prevT = t; render(); }
    function select(name) {
      cur = runs.find((r) => r.run.name === name) || cur;
      tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.run === cur.run.name)));
      t = Math.min(t, cur.span); prevT = t;
      drawBase(); render(); table(); announce();
    }
    function announce() {
      const v = instruments.values;
      status.textContent = `${cur.run.label} at ${secs(t)}: ${v.inflight.textContent} requests in flight, ` +
        `${v.delivered.textContent} images delivered, ${v.throttled.textContent} turned away.`;
    }
    // drag across the chart to move through the run
    let dragging = false;
    const toTime = (ev) => {
      const r = canvas.getBoundingClientRect(), g = base.g;
      return clamp((ev.clientX - r.left - PAD.l) / (g.w - PAD.l - PAD.r) * maxSpan, 0, cur.span);
    };
    canvas.addEventListener("pointerdown", (ev) => {
      if (!base) return;
      dragging = true; canvas.setPointerCapture(ev.pointerId); pause(); seek(toTime(ev));
    });
    canvas.addEventListener("pointermove", (ev) => { if (dragging) seek(toTime(ev)); });
    const stopDrag = () => { if (dragging) { dragging = false; announce(); } };
    canvas.addEventListener("pointerup", stopDrag);
    canvas.addEventListener("pointercancel", stopDrag);

    controllers.set(urlOf(node), { node, select, seek, play });
    // redraw on resize and theme change; stop listening once instant navigation has removed this page
    const redraw = () => {
      if (!node.isConnected) { ro.disconnect(); mo.disconnect(); pause(); return; }
      drawBase(); render();
    };
    const ro = new ResizeObserver(redraw);
    const mo = new MutationObserver(redraw);
    ro.observe(canvas);
    mo.observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
    select(cur.run.name);
  }

  // ------------------------------------------------------------------ 4. the comparison
  async function mountCompare(node) {
    let rec;
    try { rec = await recording(node); } catch (e) { return failed(node, e); }
    const longest = Math.max(...rec.runs.map((r) => r.machyon.report.duration));
    const fastest = Math.min(...rec.runs.map((r) => r.machyon.report.duration));
    const rows = rec.runs.map((r) => {
      const rep = r.machyon.report, an = r.analysis;
      return el("tr", { class: rep.duration === fastest ? "best" : null },
        el("th", { scope: "row", text: r.label }),
        el("td", {}, el("span", { class: "mx-lab-bar", style: `--w:${(rep.duration / longest * 100).toFixed(1)}%` }),
          el("span", { text: `${num(rep.duration, 1)} s` })),
        el("td", { class: (an.status_counts["429"] || 0) ? "over" : null, text: num(an.status_counts["429"] || 0) }),
        el("td", { text: num(an.server_inflight_peak) }),
        el("td", { text: num(rep.retries) }));
    });
    node.replaceChildren(el("div", { class: "mx-lab-scroll" }, el("table", { class: "mx-lab-compare-table" },
      el("thead", {},
        el("tr", {}, el("th", { text: "Run" }), el("th", { text: "Time to finish" }), el("th", { text: "Turned away (429)" }),
          el("th", { text: "Most in flight" }), el("th", { text: "Retries" })),
        el("tr", { class: "src" }, el("th"), el("th", { text: "Machyon" }), el("th", { text: "server" }),
          el("th", { text: "server" }), el("th", { text: "Machyon" }))),
      el("tbody", {}, rows))));
  }

  // ------------------------------------------------------------------ provenance
  async function mountProvenance(node) {
    let rec;
    try { rec = await recording(node); } catch (e) { return failed(node, e); }
    const p = rec.provenance;
    const commit = p.source_commit ? `commit ${p.source_commit.slice(0, 10)}${p.source_dirty ? " with local changes" : ""}`
      : "outside a git checkout, so no commit was recorded";
    node.replaceChildren(el("p", {},
      `Recorded ${p.recorded_at.slice(0, 10)} with machyon ${p.machyon_version} (${p.machyon_source || "source unknown"}, ${commit}), ` +
      `Python ${p.python_version} on ${p.platform}` + (p.cpu_count ? `, ${p.cpu_count} CPUs` : "") + ". ",
      el("a", { href: urlOf(node), text: "Download the recording" }),
      ` (JSON, schema version ${rec.schema_version}).`));
  }

  // ------------------------------------------------------------------ mount
  const MOUNTS = [["[data-lab-model]", mountModel], ["[data-lab-predict]", mountPredict],
    ["[data-lab-replay]", mountReplay], ["[data-lab-compare]", mountCompare], ["[data-lab-provenance]", mountProvenance]];
  function init() {
    if (!window.MachyonLab) return;
    for (const [sel, mount] of MOUNTS) {
      document.querySelectorAll(sel).forEach((n) => {
        if (n.dataset.labReady) return;
        n.dataset.labReady = "1";
        mount(n);
      });
    }
  }
  // the Live app (lab/live) mounts widgets on a fresh recording, and draws the same stage and instruments while it runs
  // answerKind, answerFor and placeRequests are pure: the Live app follows the same rules, and lab/tests check them
  window.MachyonLab = Object.assign(window.MachyonLab || {},
    { mountAll: init, createStage, createInstruments, answerKind, answerFor, placeRequests });
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

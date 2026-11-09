/* Machyon engine simulator: the AdaptiveLimiter's decision rules (src/machyon/limiter.py, `_adjust` and
   `_grow`), ported line for line and driven by a simple, seeded model of a server. Runs entirely in the
   browser: no network, no dependencies. Markup: <div class="mx-sim" data-mx-sim></div>.

   The server model is deliberately simple (Little's law with a capacity cap, plus scenario-specific 429s or
   errors); it exists to show how the rules react, not to predict real throughput. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ the limiter (port of limiter.py)
  class Limiter {
    constructor(initial = 32, minimum = 2, maximum = 256) {
      this.minimum = minimum; this.maximum = maximum;
      this.limit = Math.min(maximum, Math.max(minimum, initial));
      this.ceiling = maximum; this.slowStart = true; this.prev = null;
      this.skipNext = false; this.plateau = 0; this.calm = 0;
    }
    set(n, reason) {
      n = Math.min(this.maximum, Math.max(this.minimum, Math.trunc(n)));
      if (n !== this.limit) { this.limit = n; this.skipNext = true; }
      return reason;
    }
    grow(L, reason, probe = false) {
      const cap = Math.min(this.ceiling, this.maximum);
      if (L >= cap) return null;
      const step = this.slowStart && !probe ? L * 2 : L + Math.max(1, Math.floor(L / 8));
      return this.set(Math.min(cap, step), reason);
    }
    /** One measurement window. Returns the decision taken, or null. */
    adjust(w) {
      const rate = w.done / w.elapsed, L = this.limit;
      if (w.throttle && w.throttle >= Math.max(1, 0.01 * w.attempts)) {
        this.ceiling = Math.max(this.minimum, Math.trunc(L * 0.9));
        this.slowStart = false; this.prev = null; this.calm = 0;
        return this.set(L * 0.7, "throttled");
      }
      if (w.attempts >= 10 && w.errors > 0.25 * w.attempts) {
        this.slowStart = false; this.prev = null;
        return this.set(L * 0.8, "errors");
      }
      this.calm += 1;
      let relaxed = null;
      if (this.calm % 15 === 0 && this.ceiling < this.maximum) {
        this.ceiling = Math.min(this.maximum, Math.trunc(this.ceiling * 1.1) + 1);
        relaxed = "ceiling relaxed";
      }
      if (this.skipNext) { this.skipNext = false; return relaxed || "warm-up skipped"; }
      const prev = this.prev;
      this.prev = [L, rate];
      if (prev === null) return this.grow(L, "start") || relaxed;
      const [pL, pRate] = prev;
      if (L > pL && rate > pRate * 1.08) return this.grow(L, "gain") || relaxed;
      if (L > pL) { this.slowStart = false; this.prev = prev; return this.set(pL, "revert"); }
      if (L === pL) {
        this.plateau += 1;
        if (this.plateau >= 4) { this.plateau = 0; return this.grow(L, "probe", true) || relaxed; }
        return relaxed;
      }
      this.slowStart = false; this.plateau = 0;
      return relaxed;
    }
  }

  // ------------------------------------------------------------------ server scenarios
  const SCENARIOS = {
    cdn: { label: "Fast CDN, fast link", rtt: 0.25, capacity: 900,
      about: "Plenty of capacity: slow start doubles the limit until extra connections stop paying off." },
    home: { label: "Home connection (bandwidth-bound)", rtt: 0.2, capacity: 120,
      about: "The link is full at about 24 connections. Growing buys nothing, so the limiter reverts: same speed, fewer sockets." },
    limited: { label: "Rate-limited CDN (48 in flight)", rtt: 0.1, capacity: 1000, maxInflight: 48,
      about: "Above 48 requests in flight the CDN answers 429. The limiter cuts to 70%, remembers a ceiling, and relaxes it slowly." },
    flaky: { label: "Server that buckles above 100", rtt: 0.3, capacity: 600, breaking: 100,
      about: "Past 100 concurrent requests the server starts failing. More than 25% errors in a window cuts the limit to 80%." },
  };

  function rng(seed) {   // mulberry32: the same scenario always replays the same way
    return () => {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /** Run the limiter for `seconds` against a scenario; returns one point per measurement window. */
  function simulate(key, seconds = 60) {
    const s = SCENARIOS[key], rand = rng(7 + key.length), lim = new Limiter();
    const points = [];
    let t = 0, warm = false;
    while (t < seconds) {
      const L = lim.limit;
      const noise = 1 + (rand() - 0.5) * 0.08;
      const offered = (L / s.rtt) * (warm ? 0.82 : 1);             // new sockets pay a handshake first
      let attemptsPerS = Math.min(offered, s.capacity) * noise, throttleFrac = 0, errorFrac = 0;
      if (s.maxInflight && L > s.maxInflight) throttleFrac = (L - s.maxInflight) / L;
      if (s.breaking && L > s.breaking) errorFrac = Math.min(0.9, ((L - s.breaking) / L) * 2.2);
      // A window lasts >= 1 s and about one turn of the pool (min(L, 64) attempts), at most 5 s.
      let elapsed = Math.max(1, Math.min(5, Math.min(L, 64) / attemptsPerS));
      if (throttleFrac) elapsed = 1;
      const attempts = Math.round(attemptsPerS * elapsed);
      const throttle = Math.round(attempts * throttleFrac), errors = Math.round(attempts * errorFrac);
      const done = attempts - throttle - errors;
      const decision = lim.adjust({ done, attempts, throttle, errors, elapsed });
      t += elapsed;
      points.push({ t, limit: L, next: lim.limit, ceiling: lim.ceiling, rate: done / elapsed, decision });
      warm = lim.limit > L;
    }
    return points;
  }

  // ------------------------------------------------------------------ drawing
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function colors(el) {
    const cs = getComputedStyle(el);
    const v = (name, fallback) => (cs.getPropertyValue(name) || "").trim() || fallback;
    return { ink: v("--mx-ink", "#2B3C52"), muted: v("--mx-muted", "#4F637D"), line: v("--mx-band2", "#EAF3FC"),
             accent: v("--mx-accent", "#8DBDEB"), mark: v("--mx-accent-ink", "#173B5E"), ground: v("--mx-surface", "#fff"),
             soft: v("--mx-muted", "#4F637D"), font: v("--md-text-font-family", "sans-serif") };
  }

  function draw(canvas, pts, upto, hover) {
    const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== Math.round(W * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
    const g = canvas.getContext("2d"), c = colors(canvas);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const padL = 44, padR = 12, gap = 26, top = 18;
    const panelH = (H - top - gap - 26) / 2;
    const tMax = pts[pts.length - 1].t, x = (t) => padL + (t / tMax) * (W - padL - padR);
    const shown = pts.slice(0, upto);
    const maxRate = Math.max(...pts.map((p) => p.rate)) * 1.1;
    const nice = (v) => { const m = 10 ** Math.floor(Math.log10(v)); return Math.ceil(v / m * 2) / 2 * m; };
    const maxLimit = Math.min(256, nice(1.15 * Math.max(...pts.map((p) => Math.max(p.limit, p.next,
      p.ceiling < 256 ? p.ceiling : 0)))));
    const panels = [
      { y0: top, title: "Concurrency limit (in-flight requests)", max: maxLimit, val: (p) => p.limit },
      { y0: top + panelH + gap, title: "Throughput (successful responses / s)", max: maxRate, val: (p) => p.rate },
    ];
    g.font = `600 11px ${c.font}`;
    for (const P of panels) {
      const y = (v) => P.y0 + panelH - (v / P.max) * panelH;
      g.fillStyle = c.muted; g.textAlign = "left"; g.fillText(P.title, padL, P.y0 - 6);
      g.strokeStyle = c.line; g.lineWidth = 1; g.textAlign = "right"; g.font = `11px ${c.font}`;
      for (const f of [0, 0.5, 1]) {                     // recessive grid: three lines
        const yy = Math.round(y(P.max * f)) + 0.5;
        g.beginPath(); g.moveTo(padL, yy); g.lineTo(W - padR, yy); g.stroke();
        g.fillText(String(Math.round(P.max * f)), padL - 6, yy + 4);
      }
      g.font = `600 11px ${c.font}`;
      if (!shown.length) continue;
      if (P === panels[0] && shown.some((p) => p.ceiling < 256)) {   // ceiling, once one exists: dashed
        g.setLineDash([4, 4]); g.strokeStyle = c.soft; g.lineWidth = 1.5; g.beginPath();
        let on = false;
        for (const p of shown) {
          if (p.ceiling >= 256) { on = false; continue; }
          on ? g.lineTo(x(p.t), y(p.ceiling)) : g.moveTo(x(p.t), y(p.ceiling));
          on = true;
        }
        g.stroke(); g.setLineDash([]);
      }
      g.strokeStyle = c.ink; g.lineWidth = 2; g.lineJoin = "round"; g.beginPath();
      let prevX = x(0);
      shown.forEach((p, i) => {                          // the limit is a step function; the rate a line
        const xx = x(p.t), yy = y(P.val(p));
        if (P === panels[0]) { i ? g.lineTo(prevX, yy) : g.moveTo(prevX, yy); g.lineTo(xx, yy); }
        else { i ? g.lineTo(xx, yy) : g.moveTo(xx, yy); }
        prevX = xx;
      });
      g.stroke();
      if (P === panels[0]) {                              // decisions that changed the limit
        for (const p of shown) {
          if (p.next === p.limit) continue;
          g.fillStyle = c.accent; g.strokeStyle = c.mark; g.lineWidth = 1.5;
          g.beginPath(); g.arc(x(p.t), y(p.next), 4.5, 0, Math.PI * 2); g.fill(); g.stroke();
        }
        const last = shown[shown.length - 1];               // direct labels for the two series
        const hasCeil = last.ceiling < 256, above = !hasCeil || last.ceiling <= last.next;
        g.textAlign = "right"; g.fillStyle = c.ink;      // labels on opposite sides so they never collide
        g.fillText(`limit ${last.next}`, W - padR, above ? Math.max(P.y0 + 12, y(last.next) - 8) : y(last.next) + 16);
        if (hasCeil) {
          g.fillStyle = c.muted;
          g.fillText(`ceiling ${last.ceiling}`, W - padR, above ? y(last.ceiling) + 16 : Math.max(P.y0 + 12, y(last.ceiling) - 8));
        }
      }
    }
    g.fillStyle = c.muted; g.textAlign = "center"; g.font = `11px ${c.font}`;
    for (let s = 0; s <= tMax; s += 10) g.fillText(`${s}s`, x(s), H - 6);
    if (hover) {                                          // crosshair
      g.strokeStyle = c.soft; g.lineWidth = 1; g.beginPath();
      g.moveTo(Math.round(x(hover.t)) + 0.5, top); g.lineTo(Math.round(x(hover.t)) + 0.5, H - 20); g.stroke();
    }
    return { x, tMax, padL, padR };
  }

  // ------------------------------------------------------------------ widget
  function mount(root) {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    root.innerHTML = `
      <div class="mx-sim-controls">
        <label>Server <select aria-label="Server scenario">${Object.entries(SCENARIOS)
          .map(([k, s]) => `<option value="${k}"${k === "limited" ? " selected" : ""}>${s.label}</option>`).join("")}</select></label>
        <button type="button">Replay</button>
        <span class="mx-sim-about"></span>
      </div>
      <div class="mx-sim-plot" style="position:relative">
        <canvas height="300" style="height:300px" role="img"></canvas>
        <div class="mx-sim-tip" hidden></div>
      </div>
      <div class="mx-sim-stats" aria-live="polite"></div>
      <details class="mx-sim-table"><summary>Every decision, as a table</summary><div></div></details>`;
    const sel = root.querySelector("select"), btn = root.querySelector("button");
    const canvas = root.querySelector("canvas"), tip = root.querySelector(".mx-sim-tip");
    const about = root.querySelector(".mx-sim-about"), stats = root.querySelector(".mx-sim-stats");
    const table = root.querySelector(".mx-sim-table div");
    let pts = [], shown = 0, raf = 0, geo = null, hover = null;

    const stat = (label, value) => `<div>${label}<b>${value}</b></div>`;
    function update() {
      geo = draw(canvas, pts, shown, hover);
      const p = pts[Math.max(0, shown - 1)];
      const changes = pts.slice(0, shown).filter((q) => q.next !== q.limit).length;
      stats.innerHTML = stat("Time", `${p.t.toFixed(0)} s`) + stat("Limit", p.next) +
        stat("Ceiling", p.ceiling >= 256 ? "none" : p.ceiling) + stat("Throughput", `${Math.round(p.rate)}/s`) +
        stat("Last decision", p.decision || "hold") + stat("Changes", changes);
    }
    function run() {
      cancelAnimationFrame(raf);
      const key = sel.value;
      pts = simulate(key);
      about.textContent = SCENARIOS[key].about;
      canvas.setAttribute("aria-label", `Simulated run on "${SCENARIOS[key].label}": the limit ends at ` +
        `${pts[pts.length - 1].next} with throughput near ${Math.round(pts[pts.length - 1].rate)} per second.`);
      table.innerHTML = `<table><thead><tr><th>Time</th><th>Limit</th><th>Throughput/s</th><th>Decision</th><th>New limit</th></tr></thead><tbody>${
        pts.filter((p) => p.decision && p.decision !== "warm-up skipped").map((p) =>
          `<tr><td>${p.t.toFixed(1)} s</td><td>${p.limit}</td><td>${Math.round(p.rate)}</td><td>${p.decision}</td><td>${p.next}</td></tr>`).join("")
      }</tbody></table>`;
      if (reduceMotion()) { shown = pts.length; update(); return; }
      shown = 0;
      const t0 = performance.now(), simPerMs = pts[pts.length - 1].t / 6000;   // a minute in six seconds
      const step = (now) => {
        const simT = (now - t0) * simPerMs;
        shown = Math.max(1, pts.findIndex((p) => p.t > simT));
        if (shown <= 0 || simT >= pts[pts.length - 1].t) shown = pts.length;
        update();
        if (shown < pts.length) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    }
    canvas.addEventListener("pointermove", (e) => {
      if (!geo || !shown) return;
      const r = canvas.getBoundingClientRect(), t = ((e.clientX - r.left - geo.padL) / (r.width - geo.padL - geo.padR)) * geo.tMax;
      const p = pts.slice(0, shown).reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
      hover = p; geo = draw(canvas, pts, shown, hover);
      tip.hidden = false;
      tip.innerHTML = `<b>${p.t.toFixed(1)} s</b><br>limit ${p.limit}${p.next !== p.limit ? ` → ${p.next}` : ""}<br>` +
        `${Math.round(p.rate)} responses/s${p.decision ? `<br>${p.decision}` : ""}`;
      const left = Math.min(r.width - 150, Math.max(0, geo.x(p.t) + 10));
      tip.style.left = `${left}px`;
    });
    canvas.addEventListener("pointerleave", () => { hover = null; tip.hidden = true; if (geo) geo = draw(canvas, pts, shown, null); });
    sel.addEventListener("change", run);
    btn.addEventListener("click", run);
    new ResizeObserver(() => pts.length && update()).observe(canvas);
    new MutationObserver(() => pts.length && update())       // repaint when the colour scheme changes
      .observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
    run();
  }

  const init = () => document.querySelectorAll("[data-mx-sim]").forEach(mount);
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  // Exposed for tests (tools/check_sim.mjs) and the curious.
  window.MachyonSim = { Limiter, simulate, SCENARIOS };
})();

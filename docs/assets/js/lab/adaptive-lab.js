/* Adaptive Concurrency Lab (docs/lab/adaptive-concurrency.md).

   Four widgets, each mounted on an element with a data attribute:
     [data-lab-model]       an idealised server, labelled as a model: nothing in it is measured
     [data-lab-predict]     a prediction whose answer is computed from the recording, never hard-coded
     [data-lab-replay]      a replay of the recorded server events (what the server saw), plus Machyon's own Report
     [data-lab-compare]     the four runs side by side
     [data-lab-provenance]  where and how the recording was made

   Numbers measured at the server and numbers reported by Machyon are labelled and never drawn on the same axis.
   Needs recording.js (MachyonLab.loadRecording / eventsOf). No network beyond this site, no dependencies. */
(() => {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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

  function colors(node) {
    const cs = getComputedStyle(node);
    const v = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
    const dark = document.body.getAttribute("data-md-color-scheme") === "slate";
    return { ink: v("--mx-ink", "#2B3C52"), muted: v("--mx-muted", "#4F637D"), grid: v("--mx-band2", "#EAF3FC"),
             // the strong line: dark ink on paper, the bright accent on slate
             line: dark ? v("--mx-accent", "#8EA2FF") : v("--mx-accent-ink", "#173B5E"), accent: v("--mx-accent", "#8DBDEB"),
             over: v("--mx-lab-over", "#C0392B"), surface: v("--mx-surface", "#FFFFFF"),
             font: v("--md-text-font-family", "sans-serif") };
  }

  /** Per-run lookups built once from the decoded server events. */
  function indexRun(run) {
    const evs = window.MachyonLab.eventsOf(run);
    const t = new Float64Array(evs.length);
    const inflight = new Int32Array(evs.length);
    const ok = [];
    const throttled = [];
    evs.forEach((e, i) => {
      t[i] = e.t_ms;
      inflight[i] = e.inflight;
      if (e.event === "end") {
        if (e.status === 200 && e.outcome === "complete") ok.push(e.t_ms);
        else if (e.status === 429) throttled.push(e.t_ms);
      }
    });
    return { run, t, inflight, ok, throttled, span: run.analysis.span_ms };
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

  // ------------------------------------------------------------------ the server grid (model and replay)
  function serverGrid(cells, cap) {
    const cols = 8, size = 22, gap = 6, rows = Math.ceil(cells / cols);
    const svg = el("svg:svg", { viewBox: `0 0 ${cols * (size + gap) - gap} ${rows * (size + gap) - gap}`,
      class: "mx-lab-grid", "aria-hidden": "true" });
    const rects = [];
    for (let i = 0; i < rows * cols; i++) {
      const r = el("svg:rect", { x: (i % cols) * (size + gap), y: Math.floor(i / cols) * (size + gap), width: size,
        height: size, rx: 5, class: i < cap ? "cell" : "cell beyond" });
      svg.append(r);
      rects.push(r);
    }
    let shown = -1;
    return {
      svg,
      set(n) {
        if (n === shown) return;
        shown = n;
        rects.forEach((r, i) => {
          r.classList.toggle("on", i < Math.min(n, cap));
          r.classList.toggle("on-over", i >= cap && i < n);
        });
      },
    };
  }

  // ------------------------------------------------------------------ 1. the model
  function mountModel(node) {
    const CAP = 24, RTT_MS = 100, MAX = 64;
    const grid = serverGrid(MAX, CAP);
    const slider = el("input", { type: "range", min: 1, max: MAX, value: 8, id: "mx-lab-model-n",
      "aria-describedby": "mx-lab-model-out" });
    const out = el("div", { class: "mx-lab-readout", id: "mx-lab-model-out", "aria-live": "polite" });
    // throughput curve: min(n, CAP) per RTT
    const W = 320, H = 120, P = 26;
    const x = (n) => P + (n - 1) / (MAX - 1) * (W - 2 * P);
    const y = (rate) => H - P - rate / (CAP * 1000 / RTT_MS) * (H - 2 * P);
    const d = [...Array(MAX).keys()].map((k) => `${k ? "L" : "M"}${x(k + 1).toFixed(1)},${y(Math.min(k + 1, CAP) * 1000 / RTT_MS).toFixed(1)}`).join("");
    const dot = el("svg:circle", { r: 5, class: "mx-lab-dot" });
    const curve = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, class: "mx-lab-curve", role: "img",
      "aria-label": "Model: images per second rise with requests in flight until 24, then stay flat" },
      el("svg:line", { x1: x(CAP), x2: x(CAP), y1: P - 8, y2: H - P, class: "mx-lab-limit" }),
      el("svg:text", { x: x(CAP) + 5, y: H - P - 6, class: "mx-lab-svgtext", text: "limit 24" }),
      el("svg:path", { d, class: "mx-lab-curve-line" }),
      el("svg:line", { x1: P, x2: W - P, y1: H - P, y2: H - P, class: "mx-lab-axis" }),
      el("svg:text", { x: W - P, y: H - 6, "text-anchor": "end", class: "mx-lab-svgtext", text: "requests in flight" }),
      el("svg:text", { x: P, y: P - 12, class: "mx-lab-svgtext", text: "images per second" }),
      dot);
    function update() {
      const n = Number(slider.value);
      const served = Math.min(n, CAP), turned = Math.max(0, n - CAP);
      grid.set(n);
      dot.setAttribute("cx", x(n).toFixed(1));
      dot.setAttribute("cy", y(served * 1000 / RTT_MS).toFixed(1));
      dot.classList.toggle("over", turned > 0);
      out.replaceChildren(
        el("div", {}, el("b", { text: num(served * 1000 / RTT_MS) }), " images per second"),
        el("div", { class: turned ? "over" : "" }, el("b", { text: num(turned) }),
          turned === 1 ? " request turned away (429) every round" : " requests turned away (429) every round"));
    }
    slider.addEventListener("input", update);
    node.replaceChildren(
      el("label", { for: "mx-lab-model-n", class: "mx-lab-label" }, "Requests in flight"),
      slider,
      el("div", { class: "mx-lab-model-body" },
        el("figure", { class: "mx-lab-server" }, grid.svg,
          el("figcaption", {}, "The server: 24 slots. Squares past the dashed ones are over its limit.")),
        el("div", {}, out, curve)));
    update();
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
    const firstDrop = after.findIndex((v) => v !== null && v < before);
    const answer = firstDrop === -1 ? (after.some((v) => v !== null && v > before) ? "more" : "same")
      : firstDrop === 0 ? "now" : "later";
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
    const buttons = CHOICES.map(([key, text]) => el("button", { type: "button", class: "mx-lab-choice",
      "data-key": key, text, onclick: () => choose(key) }));
    group.append(...buttons);

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
  async function mountReplay(node) {
    let rec;
    try { rec = await recording(node); } catch (e) { return failed(node, e); }
    const CAP = (rec.notes && rec.notes.server_ceiling) || null;
    const runs = rec.runs.map(indexRun);
    const maxSpan = Math.max(...runs.map((r) => r.span));
    const maxPeak = Math.max(...runs.map((r) => r.run.analysis.server_inflight_peak), CAP || 0);
    const grid = serverGrid(Math.ceil(maxPeak / 8) * 8, CAP || maxPeak);
    let cur = runs.find((r) => r.run.name === "adaptive") || runs[0];
    let t = 0, playing = false, last = 0, speed = 2, raf = 0, showOthers = false;

    // controls
    const tabs = el("div", { class: "mx-lab-tabs", role: "group", "aria-label": "Run" },
      runs.map((r) => el("button", { type: "button", class: "mx-lab-tab", "data-run": r.run.name,
        text: r.run.label, onclick: () => select(r.run.name) })));
    const playBtn = el("button", { type: "button", class: "mx-lab-btn", text: "Play", onclick: () => (playing ? pause() : play()) });
    const restartBtn = el("button", { type: "button", class: "mx-lab-btn quiet", text: "Restart",
      onclick: () => { seek(0); play(); } });
    const speedSel = el("select", { "aria-label": "Playback speed", onchange: (e) => { speed = Number(e.target.value); } },
      [1, 2, 4, 10].map((s) => el("option", { value: s, selected: s === speed, text: `${s}×` })));
    const scrub = el("input", { type: "range", min: 0, max: 1000, value: 0, class: "mx-lab-scrub",
      "aria-label": "Position in the run", oninput: (e) => { pause(); seek(e.target.value / 1000 * cur.span); } });
    const clock = el("span", { class: "mx-lab-clock" });
    const others = el("label", { class: "mx-lab-toggle" },
      el("input", { type: "checkbox", onchange: (e) => { showOthers = e.target.checked; drawBase(); render(); } }),
      " Show the other runs, faintly");

    // readouts
    const live = {};
    const serverBox = el("div", { class: "mx-lab-source" },
      el("h4", { text: "Measured at the server" }),
      ["inflight", "delivered", "throttled", "recent"].map((k) => {
        live[k] = el("b");
        const label = { inflight: "requests in flight now", delivered: "images delivered so far",
          throttled: "turned away (429) so far", recent: "turned away in the last ½ second" }[k];
        return el("div", { class: `mx-lab-stat ${k}` }, live[k], el("span", { text: label }));
      }));
    const machyonBox = el("div", { class: "mx-lab-source machyon" }, el("h4", { text: "Reported by Machyon" }));
    const status = el("p", { class: "mx-lab-sr", "aria-live": "polite" });

    // chart
    const canvas = el("canvas", { class: "mx-lab-chart", role: "img" });
    const tableBox = el("details", { class: "mx-lab-table" });
    let base = null;

    node.replaceChildren(
      tabs,
      el("div", { class: "mx-lab-stage" },
        el("figure", { class: "mx-lab-server" }, grid.svg,
          el("figcaption", {}, CAP ? `The server. Each square is a request in flight; past the first ${CAP} it is over its limit.`
            : "The server. Each square is a request in flight.")),
        el("div", { class: "mx-lab-readouts" }, serverBox, machyonBox)),
      el("div", { class: "mx-lab-controls" }, playBtn, restartBtn, speedSel, scrub, clock),
      canvas, others, tableBox, status);

    // ---- drawing
    const PAD = { l: 38, r: 12, t: 14, b: 44 };
    function geometry() {
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(280, canvas.clientWidth || node.clientWidth || 600), h = 240;
      return { dpr, w, h, x: (ms) => PAD.l + ms / maxSpan * (w - PAD.l - PAD.r),
        y: (n) => PAD.t + (1 - n / (maxPeak * 1.1)) * (h - PAD.t - PAD.b - 22) };
    }
    function drawBase() {
      const g = geometry(), c = colors(node);
      const off = document.createElement("canvas");
      off.width = Math.round(g.w * g.dpr); off.height = Math.round(g.h * g.dpr);
      const ctx = off.getContext("2d");
      ctx.scale(g.dpr, g.dpr);
      ctx.font = `11px ${c.font}`;
      // horizontal gridlines
      ctx.strokeStyle = c.grid; ctx.lineWidth = 1; ctx.fillStyle = c.muted;
      const step = maxPeak > 40 ? 16 : 8;
      for (let n = 0; n <= maxPeak * 1.1; n += step) {
        ctx.beginPath(); ctx.moveTo(PAD.l, g.y(n)); ctx.lineTo(g.w - PAD.r, g.y(n)); ctx.stroke();
        ctx.fillText(String(n), 6, g.y(n) + 4);
      }
      const bottom = g.h - PAD.b;
      for (let s = 0; s * 1000 <= maxSpan; s += maxSpan > 30000 ? 10 : 5) {
        ctx.fillText(`${s} s`, g.x(s * 1000) - 6, g.h - 6);
      }
      // the server's limit
      if (CAP) {
        ctx.save(); ctx.setLineDash([5, 4]); ctx.strokeStyle = c.over; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(PAD.l, g.y(CAP)); ctx.lineTo(g.w - PAD.r, g.y(CAP)); ctx.stroke(); ctx.restore();
        ctx.fillStyle = c.over; ctx.fillText(`server limit ${CAP}`, g.w - PAD.r - 84, g.y(CAP) - 5);
      }
      const series = (r) => r.run.analysis.series;
      const line = (r, color, width, alpha) => {
        const s = series(r);
        ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round";
        ctx.beginPath();
        s.inflight_max.forEach((v, b) => {
          const x0 = g.x(b * s.bin_ms), x1 = g.x(Math.min((b + 1) * s.bin_ms, r.span));
          if (b === 0) ctx.moveTo(x0, g.y(v)); else ctx.lineTo(x0, g.y(v));
          ctx.lineTo(x1, g.y(v));
        });
        ctx.stroke(); ctx.restore();
      };
      if (showOthers) for (const r of runs) if (r !== cur) line(r, c.muted, 1, 0.22);
      line(cur, c.line, 2.2, 1);
      // 429s per bin, as a strip under the chart
      const s = series(cur), stripTop = bottom - 16, maxT = Math.max(1, ...s.throttled_429);
      ctx.fillStyle = c.over;
      s.throttled_429.forEach((v, b) => {
        if (!v) return;
        const hgt = Math.max(2, 14 * v / maxT);
        ctx.fillRect(g.x(b * s.bin_ms), stripTop + 14 - hgt, Math.max(1.5, g.x(s.bin_ms) - g.x(0) - 0.5), hgt);
      });
      ctx.fillStyle = c.over;
      ctx.fillText("429s", g.w - PAD.r - 26, stripTop + 2);
      base = { g, img: off };
      canvas.width = off.width; canvas.height = off.height;
      canvas.style.height = `${g.h}px`;
      canvas.setAttribute("aria-label", `Requests in flight at the server over time, ${cur.run.label}` +
        (CAP ? `, with the server's limit of ${CAP}` : "") + ". Red marks underneath are 429 responses.");
    }
    function render() {
      if (!base) return;
      const ctx = canvas.getContext("2d"), g = base.g, c = colors(node);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);  // the base is transparent: clear, or old playheads pile up
      ctx.drawImage(base.img, 0, 0);
      ctx.scale(g.dpr, g.dpr);
      const n = inflightAt(cur, t);
      ctx.strokeStyle = c.ink; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(g.x(t), PAD.t); ctx.lineTo(g.x(t), g.h - PAD.b); ctx.stroke();
      ctx.fillStyle = CAP && n > CAP ? c.over : c.line;
      ctx.beginPath(); ctx.arc(g.x(t), g.y(n), 4.5, 0, Math.PI * 2); ctx.fill();
      // readouts
      grid.set(n);
      live.inflight.textContent = num(n);
      live.inflight.parentElement.classList.toggle("over", Boolean(CAP && n > CAP));
      live.delivered.textContent = num(countTo(cur.ok, t));
      live.throttled.textContent = num(countTo(cur.throttled, t));
      const recent = countTo(cur.throttled, t) - countTo(cur.throttled, t - 500);
      live.recent.textContent = num(recent);
      live.recent.parentElement.classList.toggle("over", recent > 0);
      clock.textContent = `${secs(t)} / ${secs(cur.span)}`;
      scrub.value = String(Math.round(t / cur.span * 1000));
      machyonReport(t >= cur.span);
    }
    let reportShown = null;
    function machyonReport(done) {
      const key = `${cur.run.name}:${done}`;
      if (reportShown === key) return;
      reportShown = key;
      const rep = cur.run.machyon.report;
      const h = machyonBox.firstChild;
      if (!done) {
        machyonBox.replaceChildren(h, el("p", { class: "mx-lab-wait",
          text: "Machyon reports once, when the run finishes. Play to the end to see it." }));
        return;
      }
      const rows = [
        [`${num(rep.downloaded)} / ${num(rep.total)}`, "downloaded"],
        [`${num(rep.duration, 1)} s`, "total time"],
        [num(rep.throttled), "throttled responses"],
        [num(rep.retries), "retries"],
        [`${num(rep.peak_concurrency)} → ${num(rep.final_concurrency)}`, "concurrency, peak → final"],
        [`${num(rep.latency_p50 * 1000)} / ${num(rep.latency_p95 * 1000)} ms`, "latency p50 / p95"],
      ];
      machyonBox.replaceChildren(h, ...rows.map(([v, k]) => el("div", { class: "mx-lab-stat" }, el("b", { text: v }),
        el("span", { text: k }))));
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
      if (t >= cur.span) t = 0;
      playing = true; playBtn.textContent = "Pause";
      last = performance.now();
      if (!raf) raf = requestAnimationFrame(frame);
    }
    function pause() {
      playing = false; playBtn.textContent = "Play";
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      announce();
    }
    function seek(ms) { t = Math.max(0, Math.min(cur.span, ms)); render(); }
    function select(name) {
      cur = runs.find((r) => r.run.name === name) || cur;
      tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.run === cur.run.name)));
      t = Math.min(t, cur.span);
      drawBase(); render(); table(); announce();
    }
    function announce() {
      status.textContent = `${cur.run.label} at ${secs(t)}: ${live.inflight.textContent} requests in flight, ` +
        `${live.delivered.textContent} images delivered, ${live.throttled.textContent} turned away.`;
    }

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
        el("td", { text: num(an.status_counts["429"] || 0) }),
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
  // the Live app (lab/live) mounts new widgets on a fresh recording and draws the same server grid while it runs
  window.MachyonLab = Object.assign(window.MachyonLab || {}, { mountAll: init, serverGrid });
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

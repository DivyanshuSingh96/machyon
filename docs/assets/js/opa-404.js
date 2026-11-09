/* The 404 page's fetch game. Opa's five eyes follow the pointer; lost image cards drift around; click one and
   it flies into Opa's claw. Fetch all five: zero lost.

   The artwork is the brand's own symbol file, read at runtime and split into its parts, so the page always shows
   the approved Opa. Only the pupils move (each inside its own eye) and the eyes blink: the body and the curl are
   never stretched, rotated or recoloured (BRAND.md). No network beyond this site, no dependencies.
   Markup: <div data-opa-404 data-symbol="…/opa-symbol-light.svg">. Activates only where that element exists. */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const CLAW = { x: 352, y: 285 };                 // centre of the claw, in the symbol's own coordinates
  const STRAYS = ["IMG_0404.jpg", "lost-cat.png", "page.webp", "sunset.avif", "404.gif"];
  const SPOTS = [[6, 14], [80, 8], [88, 58], [4, 66], [70, 84]];   // % positions around Opa, clear of the art
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const subpaths = (d) => d.trim().split(/(?=M)/).filter(Boolean);
  const box = (d) => {
    const n = d.match(/-?\d+(?:\.\d+)?/g).map(Number), xs = n.filter((_, i) => !(i % 2)), ys = n.filter((_, i) => i % 2);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, r: Math.max(x1 - x0, y1 - y0) / 2 };
  };
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  };

  /** Build an inline Opa from the brand symbol, with each eye (eyeball + pupil) as its own group. */
  async function buildOpa(url) {
    const src = new DOMParser().parseFromString(await (await fetch(url)).text(), "image/svg+xml");
    const d = (id) => src.getElementById(id).getAttribute("d");
    const svg = el("svg", { viewBox: src.documentElement.getAttribute("viewBox"), class: "opa-art", role: "img",
      "aria-label": "Opa, the Machyon mascot, watching the page with five eyes" });
    el("path", { d: d("body"), class: "mx-ink", "fill-rule": "evenodd" }, svg);
    el("path", { d: d("claw"), class: "mx-amber opa-claw", "fill-rule": "evenodd" }, svg);
    const balls = subpaths(d("eyeball")).map((p) => ({ p, b: box(p) }));
    const eyes = subpaths(d("pupil")).map((p) => {
      const pb = box(p);                         // pair each pupil with the eyeball that contains it
      const ball = balls.reduce((a, c) => (Math.hypot(c.b.cx - pb.cx, c.b.cy - pb.cy) <
                                           Math.hypot(a.b.cx - pb.cx, a.b.cy - pb.cy) ? c : a));
      const g = el("g", { class: "opa-eye" }, svg);
      g.style.transformOrigin = `${ball.b.cx}px ${ball.b.cy}px`;
      el("path", { d: ball.p, class: "mx-amber", "fill-rule": "evenodd" }, g);
      const pupil = el("path", { d: p, class: "mx-pupil opa-pupil", "fill-rule": "evenodd" }, g);
      // how far this pupil may travel and still stay inside its eye
      const room = Math.max(1.5, ball.b.r - pb.r - Math.hypot(pb.cx - ball.b.cx, pb.cy - ball.b.cy) - 1.5);
      return { g, pupil, cx: pb.cx, cy: pb.cy, room };
    });
    return { svg, eyes };
  }

  function mount(stage) {
    if (stage.dataset.ready) return;
    stage.dataset.ready = "1";
    buildOpa(stage.dataset.symbol).then(({ svg, eyes }) => play(stage, svg, eyes)).catch(() => {
      delete stage.dataset.ready;                // keep the static fallback image
    });
  }

  function play(stage, svg, eyes) {
    stage.querySelectorAll(".opa-fallback").forEach((img) => img.remove());   // both the light and dark one
    stage.prepend(svg);
    const counter = document.querySelector(".opa-count");
    const again = document.querySelector(".opa-again");
    let fetched = 0, target = null, raf = 0;

    // --- eyes: look at the pointer (or at a flying card), blink now and then
    const toSvg = (x, y) => {
      const m = svg.getScreenCTM();
      return m ? new DOMPoint(x, y).matrixTransform(m.inverse()) : null;
    };
    const look = () => {
      raf = 0;
      if (!target) return;
      const p = toSvg(target.x, target.y);
      if (!p) return;
      for (const e of eyes) {
        const dx = p.x - e.cx, dy = p.y - e.cy, dist = Math.hypot(dx, dy) || 1, k = Math.min(1, dist / 260) * e.room / dist;
        e.pupil.setAttribute("transform", `translate(${(dx * k).toFixed(2)} ${(dy * k).toFixed(2)})`);
      }
    };
    const lookAt = (x, y) => { target = { x, y }; if (!raf) raf = requestAnimationFrame(look); };
    window.addEventListener("pointermove", (e) => lookAt(e.clientX, e.clientY), { passive: true });
    window.addEventListener("pointerdown", (e) => lookAt(e.clientX, e.clientY), { passive: true });
    const blink = () => {
      if (!reduceMotion()) {
        for (const e of eyes) { e.g.classList.remove("opa-blink"); void e.g.getBoundingClientRect(); e.g.classList.add("opa-blink"); }
      }
      setTimeout(blink, 2600 + Math.random() * 3400);
    };
    setTimeout(blink, 1800);

    // --- the strays
    const icon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="4.5" width="19" height="15" rx="3"/>' +
                 '<circle cx="8.5" cy="10" r="2"/><path d="M4 18l5.5-5.5 3.5 3.5 2.5-2.5 4.5 4.5"/></svg>';
    const spawn = () => {
      stage.querySelectorAll(".opa-stray").forEach((s) => s.remove());
      fetched = 0;
      counter.textContent = `0 of ${STRAYS.length} fetched`;
      again.hidden = true;
      stage.classList.remove("opa-done");
      STRAYS.forEach((name, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "opa-stray";
        b.style.left = `${SPOTS[i][0]}%`;
        b.style.top = `${SPOTS[i][1]}%`;
        b.style.setProperty("--d", `${5 + i * 1.3}s`);
        b.style.setProperty("--delay", `${-i * 0.9}s`);
        b.innerHTML = `${icon}<span>${name}</span>`;
        b.setAttribute("aria-label", `Fetch ${name}`);
        b.addEventListener("click", () => fetchStray(b));
        stage.appendChild(b);
      });
    };

    function fetchStray(b) {
      if (b.dataset.gone) return;
      b.dataset.gone = "1";
      const m = svg.getScreenCTM(), r = b.getBoundingClientRect();
      const claw = m ? new DOMPoint(CLAW.x, CLAW.y).matrixTransform(m) : { x: r.left, y: r.top };
      const dx = claw.x - (r.left + r.width / 2), dy = claw.y - (r.top + r.height / 2);
      const done = () => {
        b.remove();
        fetched += 1;
        counter.textContent = fetched === STRAYS.length
          ? `All ${STRAYS.length} fetched. Zero lost.` : `${fetched} of ${STRAYS.length} fetched`;
        for (const e of eyes) { e.g.classList.remove("opa-happy"); void e.g.getBoundingClientRect(); e.g.classList.add("opa-happy"); }
        if (fetched === STRAYS.length) { stage.classList.add("opa-done"); again.hidden = false; again.focus({ preventScroll: true }); }
        else stage.querySelector(".opa-stray:not([data-gone])")?.focus({ preventScroll: true });
      };
      if (reduceMotion()) { done(); return; }
      b.classList.add("opa-flying");
      b.style.transform = `translate(${dx}px, ${dy}px) scale(.18)`;
      const follow = () => {                       // the eyes track the card all the way to the claw
        if (!b.isConnected) return;
        const q = b.getBoundingClientRect();
        lookAt(q.left + q.width / 2, q.top + q.height / 2);
        requestAnimationFrame(follow);
      };
      follow();
      b.addEventListener("transitionend", done, { once: true });
      setTimeout(() => b.isConnected && done(), 900);   // in case the transition never fires
    }

    again.addEventListener("click", spawn);
    spawn();
  }

  const init = () => document.querySelectorAll("[data-opa-404]").forEach(mount);
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

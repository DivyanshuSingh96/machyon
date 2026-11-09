/* The 404 page's fetch game. Lost photos drift around Opa; drag one into the claw or click it, and Opa fetches it.
   One of them, IMG_0404.jpg, is really an HTML error page: Opa checks it, refuses it, and records it, the way
   Machyon does. Ends on "4 downloaded, 1 recorded. Zero lost."

   The artwork is the brand's own symbol file, read at runtime and split into its parts, so the page always shows
   the approved Opa. Only the pupils move (each inside its own eye) and the eyes blink: the body and the curl are
   never stretched, rotated or recoloured (BRAND.md). No network beyond this site, no dependencies.
   Markup: <div data-opa-404 data-symbol="…/opa-symbol-light.svg">. Activates only where that element exists. */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const CLAW = { x: 352, y: 285 };                 // centre of the claw, in the symbol's own coordinates
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Tiny scenes, 72 x 54. Ids are prefixed so they can't collide with the page.
  const SCENES = {
    sunset: '<defs><linearGradient id="opa-sk1" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FF8A6B"/>' +
      '<stop offset="1" stop-color="#FFD3A1"/></linearGradient></defs><rect width="72" height="54" fill="url(#opa-sk1)"/>' +
      '<circle cx="36" cy="33" r="11" fill="#FFF1C9"/><rect y="33" width="72" height="21" fill="#3E6A9E"/>' +
      '<path d="M14 39h14M40 43h18M22 47h10M48 38h10" stroke="#FFE3B8" stroke-width="1.6" stroke-linecap="round"/>',
    cat: '<rect width="72" height="54" fill="#CFE4F5"/><rect y="42" width="72" height="12" fill="#9DBBD6"/>' +
      '<path d="M48 44c8 0 12-6 9-12" stroke="#2B3C52" stroke-width="3.2" fill="none" stroke-linecap="round"/>' +
      '<ellipse cx="38" cy="36" rx="11" ry="10" fill="#2B3C52"/><circle cx="31" cy="23" r="7.5" fill="#2B3C52"/>' +
      '<path d="M25 19l1.5-8 5 5zM37 19l-1.5-8-5 5z" fill="#2B3C52"/>' +
      '<circle cx="28.5" cy="22.5" r="1.3" fill="#E9F1C6"/><circle cx="33.5" cy="22.5" r="1.3" fill="#E9F1C6"/>',
    peaks: '<rect width="72" height="54" fill="#BFE0FF"/><circle cx="56" cy="13" r="5" fill="#FFFFFF"/>' +
      '<path d="M0 54L22 20l14 18 10-12 26 28z" fill="#5F84B3"/><path d="M0 54l18-20 16 20z" fill="#3D5E89"/>' +
      '<path d="M22 20l5 8-3 1-2-3-3 3-2-2zM46 26l4 5-3-1-2 2-2-2z" fill="#FFFFFF"/>' +
      '<rect y="48" width="72" height="6" fill="#4C7A57"/>',
    night: '<rect width="72" height="54" fill="#1E2A4A"/><circle cx="52" cy="15" r="7" fill="#F4F1DE"/>' +
      '<circle cx="55" cy="13" r="6" fill="#1E2A4A"/><g fill="#F4F1DE"><circle cx="12" cy="10" r=".9"/>' +
      '<circle cx="24" cy="18" r=".7"/><circle cx="35" cy="8" r="1"/><circle cx="64" cy="30" r=".7"/>' +
      '<circle cx="18" cy="28" r=".6"/><circle cx="42" cy="24" r=".8"/></g>' +
      '<path d="M0 54V44l10-4 12 5 14-7 16 6 20-5v15z" fill="#0F1730"/>',
    // the impostor: what's really inside IMG_0404.jpg
    html: '<rect width="72" height="54" fill="#FFFFFF"/><rect width="72" height="9" fill="#E4E7EE"/>' +
      '<circle cx="5" cy="4.5" r="1.6" fill="#C9CED9"/><circle cx="10" cy="4.5" r="1.6" fill="#C9CED9"/>' +
      '<text x="6" y="22" font-family="ui-monospace,monospace" font-size="7" fill="#8A93A6">&lt;html&gt;</text>' +
      '<text x="6" y="35" font-family="system-ui,sans-serif" font-size="8" font-weight="700" fill="#2B3C52">404 Not Found</text>' +
      '<rect x="6" y="41" width="44" height="2.4" rx="1.2" fill="#DDE1EA"/><rect x="6" y="46" width="30" height="2.4" rx="1.2" fill="#DDE1EA"/>',
  };
  // x/y: desktop spot, mx/my: phone spot (% of the stage), clear of Opa in both layouts
  const STRAYS = [
    { name: "sunset.jpg", scene: "sunset", x: 2, y: 10, mx: 2, my: 0 },
    { name: "IMG_0404.jpg", scene: "html", fake: true, x: 81, y: 2, mx: 38, my: 0 },
    { name: "lost-cat.png", scene: "cat", x: 84, y: 38, mx: 74, my: 0 },
    { name: "peaks.webp", scene: "peaks", x: 5, y: 62, mx: 14, my: 75 },
    { name: "night.avif", scene: "night", x: 79, y: 73, mx: 62, my: 75 },
  ];
  const REAL = STRAYS.filter((s) => !s.fake).length;

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
    const ledger = document.querySelector(".opa-ledger");
    let ok = 0, recorded = 0, target = null, raf = 0, busy = false;

    // --- eyes: look at the pointer (or at a flying photo), blink now and then, shake "no" at the impostor
    const toSvg = (x, y) => {
      const m = svg.getScreenCTM();
      return m ? new DOMPoint(x, y).matrixTransform(m.inverse()) : null;
    };
    const look = () => {
      raf = 0;
      if (!target || busy) return;
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
    const pulse = (cls) => {
      for (const e of eyes) { e.g.classList.remove(cls); void e.g.getBoundingClientRect(); e.g.classList.add(cls); }
    };
    const blink = () => {
      if (!reduceMotion() && !busy) pulse("opa-blink");
      setTimeout(blink, 2600 + Math.random() * 3400);
    };
    setTimeout(blink, 1800);
    const shake = () => new Promise((resolve) => {
      busy = true;
      const steps = reduceMotion() ? [0] : [-1, 1, -1, 1, -0.5, 0];
      steps.forEach((s, i) => setTimeout(() => {
        for (const e of eyes) e.pupil.setAttribute("transform", `translate(${(s * e.room).toFixed(2)} 0)`);
        if (i === steps.length - 1) { busy = false; resolve(); }
      }, i * 95));
    });

    const claw = () => {
      const m = svg.getScreenCTM();
      return m ? new DOMPoint(CLAW.x, CLAW.y).matrixTransform(m) : null;
    };
    const status = () => {
      const left = STRAYS.length - ok - recorded;
      const parts = [`${ok} downloaded`];
      if (recorded) parts.push(`${recorded} recorded`);
      counter.textContent = left
        ? `${parts.join(", ")}, ${left} still lost`
        : `${ok} downloaded, ${recorded} recorded. Zero lost.`;
      if (!left) { stage.classList.add("opa-done"); again.hidden = false; again.focus({ preventScroll: true }); }
    };
    const nextFocus = () => stage.querySelector(".opa-stray:not([data-gone])")?.focus({ preventScroll: true });

    // --- the strays
    const spawn = () => {
      stage.querySelectorAll(".opa-stray").forEach((s) => s.remove());
      ok = 0; recorded = 0;
      ledger.hidden = true;
      ledger.textContent = "";
      again.hidden = true;
      stage.classList.remove("opa-done");
      STRAYS.forEach((s, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "opa-stray";
        if (s.fake) b.dataset.fake = "1";
        b.style.setProperty("--x", `${s.x}%`);
        b.style.setProperty("--y", `${s.y}%`);
        b.style.setProperty("--mx", `${s.mx}%`);
        b.style.setProperty("--my", `${s.my}%`);
        b.style.setProperty("--d", `${5 + i * 1.3}s`);
        b.style.setProperty("--delay", `${-i * 0.9}s`);
        b.style.setProperty("--tilt", `${[-4, 3, -2, 5, -3][i]}deg`);
        b.innerHTML = `<svg class="opa-photo" viewBox="0 0 72 54" aria-hidden="true">${SCENES[s.scene]}</svg>` +
                      `<span class="opa-name">${s.name}</span>`;
        b.setAttribute("aria-label", `Fetch ${s.name}`);
        b.addEventListener("click", () => {
          if (b.dataset.dragged) { delete b.dataset.dragged; return; }
          fetchStray(b);
        });
        draggable(b);
        stage.appendChild(b);
      });
      status();
    };

    // drag a photo; drop it on Opa to fetch it, anywhere else to leave it there
    function draggable(b) {
      let start = null;
      b.addEventListener("pointerdown", (e) => {
        if (b.dataset.gone || e.button !== 0) return;
        const r = b.getBoundingClientRect(), s = stage.getBoundingClientRect();
        start = { x: e.clientX, y: e.clientY, ox: e.clientX - r.left, oy: e.clientY - r.top, s, moved: false };
        b.setPointerCapture(e.pointerId);
      });
      b.addEventListener("pointermove", (e) => {
        if (!start) return;
        if (!start.moved && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 6) return;
        start.moved = true;
        b.classList.add("opa-dragging");
        b.style.left = `${e.clientX - start.s.left - start.ox}px`;
        b.style.top = `${e.clientY - start.s.top - start.oy}px`;
      });
      const end = (e) => {
        if (!start) return;
        const moved = start.moved;
        start = null;
        b.classList.remove("opa-dragging");
        if (!moved) return;
        b.dataset.dragged = "1";                 // swallow the click that follows a drag
        setTimeout(() => delete b.dataset.dragged, 0);
        const c = claw(), a = svg.getBoundingClientRect();
        const overOpa = e.clientX > a.left && e.clientX < a.right && e.clientY > a.top && e.clientY < a.bottom;
        if (overOpa || (c && Math.hypot(e.clientX - c.x, e.clientY - c.y) < 70)) fetchStray(b);
      };
      b.addEventListener("pointerup", end);
      b.addEventListener("pointercancel", end);
    }

    function fly(b, scale) {
      const c = claw(), r = b.getBoundingClientRect();
      const dx = (c ? c.x : r.left) - (r.left + r.width / 2), dy = (c ? c.y : r.top) - (r.top + r.height / 2);
      b.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
      const follow = () => {                     // the eyes track the photo all the way to the claw
        if (!b.classList.contains("opa-flying")) return;
        const q = b.getBoundingClientRect();
        lookAt(q.left + q.width / 2, q.top + q.height / 2);
        requestAnimationFrame(follow);
      };
      b.classList.add("opa-flying");
      follow();
      return new Promise((resolve) => {
        let settled = false;
        const finish = () => { if (!settled) { settled = true; resolve(); } };
        b.addEventListener("transitionend", (ev) => ev.propertyName === "transform" && finish(), { once: true });
        setTimeout(finish, 800);                 // in case the transition never fires
      });
    }

    async function fetchStray(b) {
      if (b.dataset.gone) return;
      b.dataset.gone = "1";
      b.setAttribute("aria-disabled", "true");
      const motion = !reduceMotion();
      if (!b.dataset.fake) {
        if (motion) { b.classList.add("opa-taken"); await fly(b, 0.18); }
        b.remove();
        ok += 1;
        pulse("opa-happy");
      } else {
        if (motion) await fly(b, 0.6);
        await shake();                           // Opa checks the bytes: <html>, not an image
        b.classList.remove("opa-flying");
        b.style.transform = "";                  // and drops it back where it was
        b.classList.add("opa-rejected");
        b.insertAdjacentHTML("beforeend", '<span class="opa-stamp">not_image</span>');
        b.setAttribute("aria-label", "IMG_0404.jpg was an HTML page, not an image: recorded as not_image");
        recorded += 1;
        ledger.textContent = 'failed_downloads.jsonl\n{"names": ["IMG_0404.jpg"], "reason": "not_image", ' +
          `"error": "Content-Type text/html, body starts b'<html>404 Not Found'"}`;
        ledger.hidden = false;
      }
      status();
      if (!stage.classList.contains("opa-done")) nextFocus();
    }

    again.addEventListener("click", spawn);
    spawn();
  }

  const init = () => document.querySelectorAll("[data-opa-404]").forEach(mount);
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

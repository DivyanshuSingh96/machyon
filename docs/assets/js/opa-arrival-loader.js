/* Opa's arrival on the homepage: decides whether it plays, and only then loads it.

   It plays on the homepage only, once per visit: the first time the homepage is shown in a tab, and again only
   after a reload. Moving around the site (and coming back) never replays it. It never plays with reduced motion,
   with the browser's data saver, without WebGL2, or when the logo is not on screen. Everything it needs
   (opa-arrival.js, opa-arrival-render.js and opa.glb, about 230 KB compressed) is fetched only when it is going to
   play, and leaving the homepage mid-flight removes it at once. The page never waits for it, and the drawing runs
   in a worker where the browser allows.

   Visitors who scroll on before Opa lands would otherwise miss him, so:
   - While he is on his way, a faint, breathing outline of him sits in the logo, where he will land, so the empty
     spot reads as "something is coming" instead of a logo that hasn't loaded.
   - If the logo leaves the screen before he lands, the flight stops (it would only play to nobody), and it starts
     again the next time the whole logo is back on screen, at most twice. Once he has landed, scrolling changes
     nothing. */
(() => {
  "use strict";
  const KEY = "mx-opa-arrived";
  const MAX_REPLAYS = 2;
  const REPLAY_LEAD_MS = 900;    // a replay starts 0.4 s after the logo is back (the first flight waits 1.3 s)
  const here = document.currentScript ? document.currentScript.src : location.href;
  const asset = (path) => new URL(path, here).href;          // relative to assets/js/, so it works under any base URL
  const nav = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || {};
  const reloaded = nav.type === "reload";
  const root = document.documentElement;
  let shown = false;     // this document has had its arrival (instant navigation keeps the document)
  let current = null;    // a running arrival
  let bundle = null;     // the loaded player script
  let token = 0;         // the arrival that may still act; anything older has been superseded
  let io = null, inView = true, waiting = false, replays = 0, scrolledAway = false;

  function seen() { try { return sessionStorage.getItem(KEY) === "1"; } catch (_) { return false; } }
  function remember() { try { sessionStorage.setItem(KEY, "1"); } catch (_) { /* storage blocked */ } }

  function able(mark) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
    if (navigator.connection && navigator.connection.saveData) return false;
    if (!("WebGL2RenderingContext" in window)) return false;
    const r = mark.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight;               // the logo is on screen (not a restored scroll position)
  }

  function eligible(mark) {
    if (shown) return false;
    if (seen() && !reloaded) return false;
    return able(mark);
  }

  function load() {
    if (window.MachyonOpaArrival) return Promise.resolve();
    if (!bundle) {
      bundle = new Promise((ok, fail) => {
        const s = document.createElement("script");
        s.src = asset("opa-arrival.js"); s.async = true; s.onload = ok;
        s.onerror = () => { bundle = null; fail(); };       // a later replay may try again
        document.head.append(s);
      });
    }
    return bundle;
  }

  // The outline of Opa in the logo while he is on his way. A copy of the logo's own symbol, so it is exactly where
  // and how big he will be; machyon.css makes it faint and shows it only while the arrival runs.
  function ghost(mark, on) {
    mark.querySelector(".mx-opa-ghost")?.remove();
    const opa = mark.querySelector(".mx-opa");
    if (!on || !opa) return;
    const copy = opa.cloneNode(true);
    copy.classList.remove("mx-opa");
    copy.classList.add("mx-opa-ghost");
    copy.setAttribute("aria-hidden", "true");
    opa.before(copy);
  }

  function end(mark) {
    root.classList.remove("mx-arriving");
    ghost(mark, false);
  }

  function start(mark, replay) {
    const run = ++token;
    shown = true; remember();
    root.classList.add("mx-arriving");                        // the logo's symbol waits for Opa
    ghost(mark, true);
    const t0 = replay ? performance.now() - REPLAY_LEAD_MS : performance.now();
    let abandoned = false;
    const giveUp = setTimeout(() => {                           // too slow this time: show the logo and forget it
      if (current || run !== token) return;
      abandoned = true; end(mark);
    }, 5000);
    const alive = () => run === token && !abandoned && mark.isConnected && inView;
    const missed = () => {                                       // stopped because the logo left the screen
      if (run !== token || abandoned || !mark.isConnected) return;
      end(mark);
      if (replays < MAX_REPLAYS) waiting = true;
    };
    load().then(() => {
      if (!alive()) { clearTimeout(giveUp); if (!inView) missed(); return; }
      return window.MachyonOpaArrival.run({
        glb: asset("../opa/opa.glb"), t0, alive,
        onEnd: () => {
          if (run !== token) return;
          current = null;
          if (scrolledAway || !inView) { scrolledAway = false; missed(); } else end(mark);
        },
      }).then((ctl) => { clearTimeout(giveUp); if (alive()) current = ctl; else ctl.stop(); });
    }).catch(() => { clearTimeout(giveUp); if (run === token) end(mark); });
  }

  function watch(mark) {
    io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) {
        inView = false;
        // Still in flight (the logo's symbol is hidden until the cross-fade at the landing): stop it for now.
        if (current && root.classList.contains("mx-arriving")) { scrolledAway = true; current.stop(); }
      } else if (e.intersectionRatio >= 0.99) {
        inView = true;
        if (waiting && !current && able(mark)) { waiting = false; replays++; start(mark, true); }
      }
    }, {threshold: [0, 0.99]});
    io.observe(mark);
  }

  function init() {
    token++;                                                    // a new page: whatever was running is superseded
    if (current) { current.stop(); current = null; }           // left the homepage mid-flight
    if (io) { io.disconnect(); io = null; }
    waiting = false; replays = 0; scrolledAway = false; inView = true;
    root.classList.remove("mx-arriving");
    const mark = document.querySelector(".mx-hero-mark");
    if (!mark || !eligible(mark)) return;
    if ("IntersectionObserver" in window) watch(mark);
    start(mark, false);
  }

  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

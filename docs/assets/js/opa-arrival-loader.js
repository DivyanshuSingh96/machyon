/* Opa's arrival on the homepage: decides whether it plays, and only then loads it.

   It plays on the homepage only, once per visit: the first time the homepage is shown in a tab, and again only
   after a reload. Moving around the site (and coming back) never replays it. It never plays with reduced motion,
   with the browser's data saver, without WebGL2, or when the logo is not on screen. Everything it needs
   (opa-arrival.js, opa-arrival-render.js and opa.glb, about 230 KB compressed) is fetched only when it is going to
   play, and leaving the homepage mid-flight removes it at once. The page never waits for it, and the drawing runs
   in a worker where the browser allows. */
(() => {
  "use strict";
  const KEY = "mx-opa-arrived";
  const here = document.currentScript ? document.currentScript.src : location.href;
  const asset = (path) => new URL(path, here).href;          // relative to assets/js/, so it works under any base URL
  const nav = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || {};
  const reloaded = nav.type === "reload";
  let shown = false;     // this document has had its arrival (instant navigation keeps the document)
  let current = null;    // a running arrival
  let bundle = null;     // the loaded player script

  function seen() { try { return sessionStorage.getItem(KEY) === "1"; } catch (_) { return false; } }
  function remember() { try { sessionStorage.setItem(KEY, "1"); } catch (_) { /* storage blocked */ } }

  function eligible(mark) {
    if (shown) return false;
    if (seen() && !reloaded) return false;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
    if (navigator.connection && navigator.connection.saveData) return false;
    if (!("WebGL2RenderingContext" in window)) return false;
    const r = mark.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight;               // the logo is on screen (not a restored scroll position)
  }

  function load() {
    if (window.MachyonOpaArrival) return Promise.resolve();
    if (!bundle) {
      bundle = new Promise((ok, fail) => {
        const s = document.createElement("script");
        s.src = asset("opa-arrival.js"); s.async = true; s.onload = ok; s.onerror = fail;
        document.head.append(s);
      });
    }
    return bundle;
  }

  function init() {
    if (current) { current.stop(); current = null; }          // left the homepage mid-flight
    document.documentElement.classList.remove("mx-arriving");
    const mark = document.querySelector(".mx-hero-mark");
    if (!mark || !eligible(mark)) return;
    shown = true; remember();
    document.documentElement.classList.add("mx-arriving");    // the logo's symbol waits for Opa
    const t0 = performance.now(), page = mark;
    let abandoned = false;
    const giveUp = setTimeout(() => {                           // too slow this time: show the logo and forget it
      if (current) return;
      abandoned = true; document.documentElement.classList.remove("mx-arriving");
    }, 5000);
    const alive = () => !abandoned && page.isConnected;
    load().then(() => {
      if (!alive()) return;
      return window.MachyonOpaArrival.run({glb: asset("../opa/opa.glb"), t0, alive, onEnd: () => { current = null; }})
        .then((ctl) => { clearTimeout(giveUp); if (alive()) current = ctl; else ctl.stop(); });
    }).catch(() => { clearTimeout(giveUp); document.documentElement.classList.remove("mx-arriving"); });
  }

  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(init);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

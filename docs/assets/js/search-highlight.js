/* Search highlights that don't stay.

   Opening a search result marks the searched words on the page (the theme's search.highlight, driven by ?h= in
   the URL). The marks used to stay until the page was reloaded, in the same blue as links and inline code, and the
   ?h= went with the URL when it was copied or shared. Now the marks are amber (machyon.css) and clear themselves:
   after a few seconds, or as soon as the reader clicks, types or scrolls. The ?h= is taken out of the address at
   once, so a reload or a shared link opens the page without them.

   The theme inserts the marks a moment after a page is shown, and a result on the page already open (a heading
   further down) marks it again without loading anything, so the marks are watched for rather than looked for. */
(() => {
  "use strict";
  const SEL = "mark[data-md-highlight]";
  const SHOW_MS = 4000;                 // how long the marks stay if the reader does nothing
  const GRACE_MS = 600;                 // input in this window is still the click or key that opened the result
  const WAIT_MS = 2000;                 // how long to wait for the theme to insert marks
  const FADE_MS = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 600;
  const INPUT = ["pointerdown", "keydown", "wheel", "touchstart"];
  let cleanup = null;

  function dropParam() {
    const url = new URL(location.href);
    if (!url.searchParams.has("h")) return;
    url.searchParams.delete("h");
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    // The theme writes absolute in-page links (the table of contents, "skip to content") from the address it had.
    for (const a of document.querySelectorAll('a[href*="h="]')) {
      const u = new URL(a.href, location.href);
      if (u.origin === location.origin && u.pathname === location.pathname && u.searchParams.has("h")) {
        u.searchParams.delete("h");
        a.setAttribute("href", u.pathname + u.search + u.hash);
      }
    }
  }

  function unwrap(marks) {
    for (const m of marks) {
      if (!m.isConnected) continue;
      const parent = m.parentNode;
      m.replaceWith(...m.childNodes);
      parent.normalize();
    }
  }

  function handle() {
    if (cleanup) cleanup();
    const marks = [...document.querySelectorAll(SEL)];
    if (!marks.length) return false;
    dropParam();
    const t0 = performance.now();
    let done = false;
    const clear = () => {
      if (done) return;
      done = true;
      stop();
      for (const m of marks) m.classList.add("mx-hl-out");
      setTimeout(() => unwrap(marks), FADE_MS);
    };
    const onInput = () => { if (performance.now() - t0 > GRACE_MS) clear(); };
    const timer = setTimeout(clear, SHOW_MS);
    INPUT.forEach((e) => addEventListener(e, onInput, { passive: true, capture: true }));
    const stop = () => {
      clearTimeout(timer);
      INPUT.forEach((e) => removeEventListener(e, onInput, { capture: true }));
      if (cleanup === stop) cleanup = null;
    };
    cleanup = stop;
    return true;
  }

  function onPage() {
    if (cleanup) cleanup();             // a new page: the previous page's marks went with it
    handle();
    setTimeout(dropParam, WAIT_MS);     // a search that matched nothing on the page still leaves ?h= behind
  }

  // Only added <mark> nodes are looked at, so pages that change their DOM often (the Lab) pay next to nothing.
  let pending = 0;
  new MutationObserver((records) => {
    if (pending) return;
    for (const r of records) for (const n of r.addedNodes) {
      if (n.nodeName === "MARK" && n.hasAttribute("data-md-highlight")) {
        pending = setTimeout(() => { pending = 0; handle(); }, 30);
        return;
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(onPage);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", onPage);
  else onPage();
})();

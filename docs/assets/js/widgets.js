/* Two things every page needs, run each time a page is shown (directly or through instant navigation):

   1. A page's interactive widgets, loaded only on the pages that have them.

   Each widget's script is fetched the first time a page containing its markup is shown, whether that page was
   opened directly or reached through the site's instant navigation, and never on other pages. The scripts mount
   themselves on that markup (through document$, which hands a late subscriber the current page), as they did when
   every page loaded them. Scripts listed together run in that order.

   2. Code blocks: the theme puts an empty link at the start of every code line (its line-selection mode uses them).
      Keyboard and screen-reader users met each one as a nameless link, a tab stop per line; they are taken out of
      the tab order and hidden from assistive technology, and still work for the mouse. */
(() => {
  "use strict";
  const WIDGETS = [
    ["[data-mx-sim]", ["engine-sim.js"]],
    ["[data-lab-model], [data-lab-predict], [data-lab-replay], [data-lab-compare], [data-lab-provenance]",
      ["lab/recording.js", "lab/adaptive-lab.js"]],
    ["[data-opa-404]", ["opa-404.js"]],
    ["[data-mx-film]", ["film.js"]],
  ];
  const here = document.currentScript ? document.currentScript.src : location.href;
  const requested = new Set();

  function load(path) {
    const src = new URL(path, here).href;
    if (requested.has(src)) return;
    requested.add(src);
    const s = document.createElement("script");
    s.src = src;
    s.async = false;                    // downloads in parallel, runs in the order added
    s.onerror = () => requested.delete(src);
    document.head.append(s);
  }

  function scan() {
    for (const [selector, scripts] of WIDGETS) if (document.querySelector(selector)) scripts.forEach(load);
    for (const a of document.querySelectorAll('.md-typeset pre a[id^="__codelineno-"]:not([tabindex])')) {
      a.tabIndex = -1;
      a.setAttribute("aria-hidden", "true");
    }
  }

  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(scan);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
})();

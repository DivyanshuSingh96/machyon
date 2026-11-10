/* "On this page": highlight the section you clicked, even near the end of a page.

   The theme highlights the section whose heading has reached the top of the window. The last few headings of a page
   often can't get there (the page ends first), so clicking one of them scrolled to it but highlighted the last
   section instead. When that happens, the clicked entry keeps the highlight until you scroll yourself; everywhere
   else the theme's own highlighting is left alone. The same applies to opening a link to such a section. */
(() => {
  "use strict";
  const TOC = ".md-sidebar--secondary";
  const LINK = `${TOC} .md-nav__link`;
  const ACTIVE = "md-nav__link--active";
  const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);
  let pinned = null;
  let observer = null;
  let timer = 0;

  const atBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;

  function apply() {
    if (!pinned || !pinned.isConnected) return release();
    for (const link of document.querySelectorAll(LINK)) {
      if (link !== pinned && link.classList.contains(ACTIVE)) link.classList.remove(ACTIVE);
    }
    if (!pinned.classList.contains(ACTIVE)) pinned.classList.add(ACTIVE);
  }

  // Wait for the scroll (smooth or instant) to stop, then keep the pin only if the page ended before the heading
  // reached the top.
  function settle() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!pinned) return;
      if (atBottom()) apply();
      else release();
    }, 150);
  }

  function release() {
    pinned = null;
    clearTimeout(timer);
    window.removeEventListener("scroll", settle);
    if (observer) observer.disconnect();
    observer = null;
  }

  function pin(link) {
    release();
    pinned = link;
    // The theme updates the highlight from its own scroll handler; put ours back whenever it does, while pinned.
    observer = new MutationObserver(() => { if (pinned && atBottom()) apply(); });
    observer.observe(link.closest(TOC), { subtree: true, attributes: true, attributeFilter: ["class"] });
    window.addEventListener("scroll", settle, { passive: true });
    settle();
  }

  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element && event.target.closest(LINK);
    if (link && link.hash) pin(link);
  });
  // The reader scrolls on their own: the theme's highlighting takes over again.
  window.addEventListener("wheel", release, { passive: true });
  window.addEventListener("touchmove", release, { passive: true });
  window.addEventListener("keydown", (event) => { if (SCROLL_KEYS.has(event.key)) release(); });

  // Arriving through a link to one of a page's last sections (from another page, or a #link on this one).
  const fromHash = () => {
    if (!location.hash) return;
    const link = [...document.querySelectorAll(LINK)].find((a) => a.hash === location.hash);
    if (link && link !== pinned) setTimeout(() => { if (atBottom()) pin(link); }, 300);
  };
  window.addEventListener("hashchange", fromHash);
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(fromHash);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fromHash);
  else fromHash();
})();

/* The product film page: chapters that jump, and a chapter list that follows the film.

   - Choosing a chapter (in the list or the transcript) seeks the film there and plays it: that click is the
     visitor's own request, so this is not autoplay. The address gets ?t=<seconds>, so the link can be shared.
   - Opening the page with ?t=<seconds> (or #t=) cues the film at that point without playing it.
   - While the film plays, the current chapter is marked (aria-current) in the list.
   Without this script the chapters are still a readable list and the film plays from the start. */
(() => {
  "use strict";

  function startFromAddress() {
    const q = new URLSearchParams(location.search).get("t") ?? new URLSearchParams(location.hash.slice(1)).get("t");
    const t = Number(q);
    return q !== null && Number.isFinite(t) && t >= 0 ? t : null;
  }

  function mount(stage) {
    if (stage.dataset.mxFilmReady) return;
    stage.dataset.mxFilmReady = "1";
    const video = stage.querySelector("video");
    const items = [...stage.querySelectorAll(".mx-film-chapters li[data-t]")];
    const starts = items.map((li) => Number(li.dataset.t));
    if (!video || !items.length) return;

    let current = -1;
    const mark = (time) => {
      let i = 0;
      while (i + 1 < starts.length && time >= starts[i + 1] - 0.05) i++;
      if (i === current) return;
      if (current >= 0) items[current].removeAttribute("aria-current");
      items[i].setAttribute("aria-current", "true");
      current = i;
      // Beside the film the list scrolls inside itself: keep the current chapter in view there (never the page).
      const list = items[i].parentElement;
      if (list.scrollHeight > list.clientHeight + 1) {
        const top = items[i].offsetTop - list.offsetTop - 6;
        const bottom = top + items[i].offsetHeight + 12;
        if (top < list.scrollTop || bottom > list.scrollTop + list.clientHeight) {
          list.scrollTo({top, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
        }
      }
    };

    const cue = (t, play) => {
      const go = () => {
        video.currentTime = Math.min(t, (video.duration || t + 1) - 0.1);
        mark(t);
        if (play) video.play().catch(() => { /* the browser declined; the film stays cued */ });
      };
      if (video.readyState >= 1) go();
      else {
        if (video.preload === "none") video.preload = "metadata";
        video.addEventListener("loadedmetadata", go, {once: true});
        video.load();
      }
    };

    // Chapter links anywhere on the page: the list and the transcript.
    for (const a of document.querySelectorAll("a.mx-film-jump[data-t]")) {
      a.addEventListener("click", (e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;   // new tab: let it open
        e.preventDefault();
        const t = Number(a.dataset.t);
        const url = new URL(location.href);
        url.searchParams.set("t", String(Math.round(t)));
        url.hash = "";
        history.replaceState(history.state, "", url.pathname + url.search);
        cue(t, true);
        const r = video.getBoundingClientRect();         // from the transcript: bring the film into view if needed
        if (r.top < 0 || r.bottom > innerHeight) {
          const smooth = !matchMedia("(prefers-reduced-motion: reduce)").matches;
          scrollTo({top: scrollY + r.top - 72, behavior: smooth ? "smooth" : "auto"});
        }
      });
    }

    video.addEventListener("timeupdate", () => mark(video.currentTime));
    video.addEventListener("seeked", () => mark(video.currentTime));

    const t = startFromAddress();
    if (t !== null) cue(t, false);
    else mark(0);
  }

  function scan() { document.querySelectorAll("[data-mx-film]").forEach(mount); }

  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(scan);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
})();

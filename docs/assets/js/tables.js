/* Tables that don't fit the screen become cards.

   A table that fits keeps its columns. One that would be wider than the text column (a phone, a narrow window)
   is shown one row per card, each value labelled with its column's heading, instead of being cut off at the
   edge. It's decided per table and again whenever the window changes size, so the same page can switch back. */
(() => {
  "use strict";
  // Markdown tables, and the tables the Lab builds while it runs.
  const TABLES = ".md-typeset table:not([class]), .md-typeset table.mx-lab-compare-table";

  // Label every cell with its column heading (once per table); the cards show the label beside the value.
  function prepare(table) {
    const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    if (!heads.length) return false;
    for (const row of table.querySelectorAll("tbody tr")) {
      [...row.children].forEach((cell, i) => {
        if (heads[i] !== undefined) cell.setAttribute("data-label", heads[i]);
      });
    }
    return true;
  }

  function room(table) {
    const box = table.closest(".md-typeset__table") || table.parentElement;
    const cs = getComputedStyle(box);
    return box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  }

  function fit() {
    for (const table of document.querySelectorAll(TABLES)) {
      if (!prepare(table)) continue;
      table.removeAttribute("data-mx-stacked");   // measure it as a table (an attribute: these tables have no class)
      if (table.scrollWidth > room(table) + 1) {
        table.setAttribute("data-mx-stacked", "");
        // keep the table's meaning for screen readers once its rows are laid out as blocks
        table.setAttribute("role", "table");
        table.querySelectorAll("thead, tbody").forEach((g) => g.setAttribute("role", "rowgroup"));
        table.querySelectorAll("tr").forEach((r) => r.setAttribute("role", "row"));
        table.querySelectorAll("th").forEach((c) => c.setAttribute("role", "columnheader"));
        table.querySelectorAll("td").forEach((c) => c.setAttribute("role", "cell"));
      }
    }
  }

  let timer = 0;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(fit, 120); };
  window.addEventListener("resize", schedule, { passive: true });
  // Tables added later (the Lab fills its tables in as a replay runs, or when a <details> opens) are fitted too.
  const watch = () => {
    const article = document.querySelector(".md-content");
    if (!article || article.dataset.mxTablesWatched) return;
    article.dataset.mxTablesWatched = "1";
    new MutationObserver((records) => {
      if (records.some((r) => [...r.addedNodes].some((n) => n.nodeType === 1 && (n.matches("table, tr") || n.querySelector("table"))))) {
        schedule();
      }
    }).observe(article, { childList: true, subtree: true });
  };
  document.addEventListener("toggle", (e) => { if (e.target.querySelector && e.target.querySelector("table")) schedule(); }, true);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);   // widths change once fonts load
  const start = () => { watch(); schedule(); };
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(start);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

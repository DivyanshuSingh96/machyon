// Runs before the page paints, so the chosen theme never flashes. The choice is a display preference only.
(function () {
  var t = null;
  try { t = localStorage.getItem("machyon-ui-theme"); } catch (e) { /* storage blocked */ }
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) document.documentElement.classList.add("motion");
})();

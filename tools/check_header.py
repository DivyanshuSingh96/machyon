"""Check the documentation header at every width and on every device: no two pieces overlap, nothing leaves the
screen, everything sits on the bar's centre line, and the search label never runs under its shortcut.

    zensical build --clean
    python tools/check_header.py site                 # prints each problem; exits 1 if there are any
    python tools/check_header.py site --sheets out/   # also saves a screenshot of the header per device

It sweeps every width from 280 to 2560 px (4 px steps) on two pages, then loads Playwright's device list (phones,
tablets and foldables, portrait and landscape, as in Chrome DevTools), Chrome DevTools' own extra devices and common
laptop and desktop sizes, each in light and dark. The pieces it checks are the header's [data-mx-item] elements.

Needs Playwright, which isn't a dependency of the project:
    pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = ["", "reliability/retries-and-rate-limits/"]
WIDTHS = range(280, 2561, 4)

#: In Chrome DevTools' device list but not in Playwright's: (name, width, height, scale, mobile, touch).
DEVTOOLS_EXTRA = [
    ("Surface Pro 7", 912, 1368, 2, True, True), ("Surface Duo", 540, 720, 2.5, True, True),
    ("Galaxy Z Fold 5", 344, 882, 2.625, True, True), ("Asus Zenbook Fold", 853, 1280, 1.5, True, True),
    ("Nest Hub", 1024, 600, 2, True, True), ("Nest Hub Max", 1280, 800, 2, True, True),
    ("iPhone 14 Pro Max", 430, 932, 3, True, True), ("Pixel 7", 412, 915, 2.625, True, True),
    ("Galaxy S20 Ultra", 412, 915, 3.5, True, True), ("Galaxy A51/71", 412, 914, 2.625, True, True),
    ("iPad Air", 820, 1180, 2, True, True), ("iPad Mini", 768, 1024, 2, True, True),
]
DESKTOPS = [(1024, 768), (1152, 864), (1280, 720), (1280, 800), (1280, 1024), (1360, 768), (1366, 768), (1440, 900),
            (1536, 864), (1600, 900), (1680, 1050), (1920, 1080), (2048, 1152), (2560, 1440), (3440, 1440), (3840, 2160)]

#: Runs in the page; returns the problems it finds in the header at the current size.
PROBE = r"""() => {
  const fallback = ['label[for=__drawer]', 'header .md-logo', '.mx-header__docs', '.mx-header__version',
    '.md-search__button', 'header label[for=__search]', '.mx-header__link', '.mx-header__rule',
    'header .md-header__option label:not([hidden])'];
  const header = document.querySelector('header.md-header');
  let els = [...header.querySelectorAll('[data-mx-item]')];
  if (!els.length) els = fallback.flatMap(s => [...header.querySelectorAll(s)]);
  const vw = document.documentElement.clientWidth;
  const row = header.querySelector('.md-header__inner').getBoundingClientRect();
  const items = [];
  for (const el of els) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (cs.display === 'none' || cs.visibility === 'hidden' || r.width < 1 || r.height < 1) continue;
    let p = el, hidden = false;
    while (p && p !== header) { const s = getComputedStyle(p); if (s.display === 'none' || s.visibility === 'hidden') { hidden = true; break; } p = p.parentElement; }
    if (hidden) continue;
    items.push({name: el.dataset.mxItem || el.getAttribute('aria-label') || el.className || el.tagName,
                l: r.left, r: r.right, t: r.top, b: r.bottom});
  }
  const problems = [];
  // everything sits on the bar's centre line: the item, and the icon or image inside it
  const mid0 = (row.top + row.bottom) / 2;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || getComputedStyle(el).display === 'none') continue;
    let p = el, hidden = false;
    while (p && p !== header) { if (getComputedStyle(p).display === 'none') { hidden = true; break; } p = p.parentElement; }
    if (hidden) continue;
    const name = el.dataset.mxItem || el.className;
    for (const box of [el, el.querySelector(':scope svg, :scope img')]) {
      if (!box) continue;
      const b = box.getBoundingClientRect();
      if (b.height && Math.abs((b.top + b.bottom) / 2 - mid0) > 2.5)
        problems.push(`off centre: ${name}${box === el ? '' : ' icon'} (${((b.top + b.bottom) / 2 - mid0).toFixed(1)} px)`);
    }
  }
  for (const a of items) {
    if (a.l < -0.5 || a.r > vw + 0.5) problems.push(`off screen: ${a.name} [${a.l.toFixed(0)}, ${a.r.toFixed(0)}] of ${vw}`);
    const mid = (a.t + a.b) / 2;
    if (mid < row.top || mid > row.bottom) problems.push(`not on the bar's row: ${a.name}`);
  }
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i], b = items[j];
    const ox = Math.min(a.r, b.r) - Math.max(a.l, b.l), oy = Math.min(a.b, b.b) - Math.max(a.t, b.t);
    if (ox > 1 && oy > 1) problems.push(`overlap: ${a.name} / ${b.name} (${ox.toFixed(0)} px)`);
  }
  // the search label must not run under the shortcut hint
  const btn = header.querySelector('.md-search__button'), lab = btn && btn.querySelector('[data-mx-label], .mx-search__label');
  if (btn && lab && btn.getBoundingClientRect().width > 0 && getComputedStyle(btn.parentElement).display !== 'none') {
    const hint = getComputedStyle(btn, '::after');
    if (hint.display !== 'none' && hint.content !== 'none') {
      const br = btn.getBoundingClientRect(), lr = lab.getBoundingClientRect();
      const hw = parseFloat(hint.width) || 0, right = parseFloat(hint.right) || 0;
      if (lr.right > br.right - right - hw - 2) problems.push(`search label runs under the shortcut (${(lr.right - (br.right - right - hw)).toFixed(0)} px)`);
    }
    if (lab.scrollWidth > lab.clientWidth + 1) problems.push('search label is cut off');
  }
  return {problems, items: items.map(i => i.name), vw, rowh: row.height};
}"""


def devices(p) -> list[tuple]:
    """Every distinct viewport: Playwright's devices, then DevTools' extras and desktops it doesn't already have."""
    out, seen = [], set()
    for name, d in p.devices.items():
        vp = d["viewport"]
        entry = (name, vp["width"], vp["height"], d.get("device_scale_factor", 1), d.get("is_mobile", False),
                 d.get("has_touch", False))
        out.append(entry)
        seen.add((entry[1], entry[2], entry[4]))
    extra = DEVTOOLS_EXTRA + [(f"Desktop {w}x{h}", w, h, 1, False, False) for w, h in DESKTOPS]
    for e in extra:
        if (e[1], e[2], e[4]) not in seen:
            seen.add((e[1], e[2], e[4]))
            out.append(e)
    return out


def key(problem: str) -> str:
    """Group "overlap: a / b (3 px)" with every other overlap of a and b."""
    return re.sub(r"[\[(].*$", "", problem).strip()


def main() -> int:
    site = Path(sys.argv[1])
    sheets = Path(sys.argv[sys.argv.index("--sheets") + 1]) if "--sheets" in sys.argv else None
    if sheets:
        sheets.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    fails: dict[str, list[str]] = {}
    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for path in PAGES:
            page.goto(base + path)
            page.wait_for_timeout(400)
            for w in WIDTHS:
                page.set_viewport_size({"width": w, "height": 800})
                for problem in page.evaluate(PROBE)["problems"]:
                    fails.setdefault(key(problem), []).append(f"{w}px /{path}")
        page.close()
        devs = devices(p)
        for name, w, h, dpr, mobile, touch in devs:
            for scheme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=min(dpr, 2),
                                          is_mobile=mobile, has_touch=touch, color_scheme=scheme)
                page = ctx.new_page()
                page.goto(base + PAGES[1])
                page.wait_for_timeout(350)
                for problem in page.evaluate(PROBE)["problems"]:
                    fails.setdefault(key(problem), []).append(f"{name} {w}x{h} {scheme}")
                if sheets:
                    f = sheets / f"{len(shots):03d}-{scheme}.png"
                    page.screenshot(path=str(f), clip={"x": 0, "y": 0, "width": w, "height": min(h, 130)})
                    shots.append((name, w, h, scheme, str(f)))
                ctx.close()
        browser.close()
    server.shutdown()
    if sheets:
        (sheets / "index.json").write_text(json.dumps({"shots": shots, "devices": len(devs)}), encoding="utf-8")
    print(f"{len(WIDTHS)} widths on {len(PAGES)} pages, {len(devs)} devices in light and dark")
    for k, where in sorted(fails.items()):
        print(f"FAIL {k}: {len(where)} cases, e.g. {where[:3]} ... {where[-1]}")
    print("no problems" if not fails else f"{len(fails)} kinds of problem")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

"""Check the built documentation site (run after `zensical build`).

    python tools/check_docs.py              # links, anchors, API references, third-party requests
    python tools/check_docs.py --release    # ...and no placeholders left anywhere

Zensical's strict build catches missing pages; this catches what it doesn't: broken #anchors, API references
mkdocstrings couldn't resolve, leftover template syntax, and pages that would load resources from other sites.
Exit status 1 on any problem.
"""
from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PLACEHOLDER = "YOUR_GITHUB_USERNAME"
RELEASE_FILES = ["zensical.toml", "pyproject.toml", "README.md", "CITATION.cff", "CONTRIBUTING.md", "docs"]
#: Requests the site may make to other origins at runtime (the theme's repository widget).
ALLOWED_REMOTE = ("https://api.github.com/",)


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.media: list[str] = []      # images, video/audio sources and posters on this site
        self.remote: list[str] = []
        self._skip = 0           # inside <code>/<pre>/<script>: template syntax there is content
        self.text_hits: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.add(a["name"])
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        src = (a.get("src") if tag in ("script", "img", "iframe", "source", "video", "audio")
               else a.get("href") if tag == "link" and a.get("rel") not in ("canonical", "alternate") else None)
        if tag == "video" and a.get("poster"):
            src = a["poster"]
        for attr in ("src", "poster"):
            v = a.get(attr)
            if v and tag in ("img", "source", "video", "audio", "script") and not urlsplit(v).scheme \
                    and not v.startswith(("data:", "//")):
                self.media.append(v)
        if src and urlsplit(src).scheme in ("http", "https") and not src.startswith(ALLOWED_REMOTE):
            self.remote.append(f"<{tag}> {src}")
        if tag in ("code", "pre", "script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("code", "pre", "script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        for pattern in (r"\{\{.*?\}\}", r"\{%.*?%\}", r"\]\[machyon\.[\w.]+\]"):
            self.text_hits += re.findall(pattern, data)


def check_site(site: Path) -> list[str]:
    problems: list[str] = []
    pages: dict[Path, Page] = {}
    for f in site.rglob("*.html"):
        p = Page()
        p.feed(f.read_text(encoding="utf-8"))
        pages[f.resolve()] = p
    for f, p in pages.items():
        rel = f.relative_to(site.resolve())
        for hit in p.text_hits:
            problems.append(f"{rel}: unrendered template or unresolved reference: {hit}")
        for r in p.remote:
            problems.append(f"{rel}: loads a third-party resource {r}")
        if rel.name == "404.html":      # served from any depth: its links are absolute by design
            continue
        for href in p.links:
            url, frag = urldefrag(href)
            if urlsplit(url).scheme or url.startswith(("mailto:", "//")):
                continue
            target = (f.parent / unquote(url)).resolve() if url else f
            if target.is_dir() or url.endswith("/"):
                target = target / "index.html"
            if url and not target.exists():
                if not target.with_suffix("").exists() and not (site / unquote(url).lstrip("/")).exists():
                    problems.append(f"{rel}: broken link {href}")
                continue
            if frag and target in pages and unquote(frag) not in pages[target].ids:
                problems.append(f"{rel}: missing anchor {href}")
        for src in p.media:                     # every image, video, poster and script must exist
            path = urldefrag(src)[0].split("?")[0]
            if not (f.parent / unquote(path)).resolve().is_file():
                problems.append(f"{rel}: missing file {src}")
    return problems


def check_release() -> list[str]:
    problems = []
    for name in RELEASE_FILES:
        path = ROOT / name
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()
                                                and p.suffix in (".md", ".html", ".toml", ".py", ".cff", ".txt")]
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if PLACEHOLDER in line:
                    problems.append(f"{f.relative_to(ROOT)}:{n}: placeholder {PLACEHOLDER}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--release", action="store_true", help="also fail on placeholders (before publishing)")
    ap.add_argument("--site", type=Path, default=SITE, help="built site folder (default: site/)")
    a = ap.parse_args(argv)
    if not (a.site / "index.html").exists():
        print(f"No built site in {a.site}; run `zensical build` first.", file=sys.stderr)
        return 1
    problems = check_site(a.site) + (check_release() if a.release else [])
    for p in problems:
        print(p)
    print(f"{'✗' if problems else '✓'} {len(problems)} problem(s) in {a.site}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

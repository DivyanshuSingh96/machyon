"""What search engines and link previews receive: the built documentation site and the package metadata.

The site tests read the HTML that `zensical build` wrote to site/ (or $MACHYON_SITE), so build first:

    zensical build --clean && python -m pytest -q tests/test_site.py

Without a built site they are skipped; the docs workflow runs them right after its strict build.
"""
from __future__ import annotations

import json
import os
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOME_TITLE = "Machyon: Fast Async Image Downloader for Python"
VERIFICATION = '<meta name="google-site-verification" content="eRLpDHbFuvuFv1DbACJxBL-dVZzBtzqf5azoMKgXZSM" />'


class Head(HTMLParser):
    """The parts of a page that search engines and link previews read."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta: dict[str, list[str]] = {}
        self.canonical: str | None = None
        self.jsonld: list[str] = []
        self.h1: list[str] = []
        self._in_head = self._in_title = self._in_ld = self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "head":
            self._in_head = True
        elif tag == "title" and self._in_head and self.title is None:   # not an SVG's <title> in the body
            self._in_title, self.title = True, ""
        elif tag == "meta" and self._in_head:
            key = a.get("property") or a.get("name")
            if key:
                self.meta.setdefault(key, []).append(a.get("content") or "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href")
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
            self.jsonld.append("")
        elif tag == "h1":
            self._in_h1 = True
            self.h1.append("")

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_ld = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld:
            self.jsonld[-1] += data
        if self._in_h1:
            self.h1[-1] += data

    def one(self, key: str) -> str:
        values = self.meta.get(key, [])
        assert len(values) == 1, f"{key}: expected one tag, found {len(values)}"
        return values[0]


@pytest.fixture(scope="module")
def site() -> dict:
    folder = Path(os.environ.get("MACHYON_SITE", ROOT / "site"))
    config = ROOT / "zensical.toml"
    if not (folder / "index.html").is_file() or not config.is_file():
        pytest.skip("no built documentation site; run `zensical build` first")
    sources = [config, *(ROOT / "docs").rglob("*.md"), *(ROOT / "docs" / "overrides").rglob("*.html")]
    newest = max(sources, key=lambda p: p.stat().st_mtime)
    built = max(f.stat().st_mtime for f in folder.rglob("*") if f.is_file())
    if newest.stat().st_mtime > built:
        pytest.fail(f"{folder} is older than {newest.relative_to(ROOT).as_posix()}: "
                    "run `zensical build --clean` first", pytrace=False)
    site_url = tomllib.loads(config.read_text(encoding="utf-8"))["project"]["site_url"]
    pages = {}
    for f in sorted(folder.rglob("*.html")):
        rel = f.relative_to(folder).as_posix()
        if rel.startswith("assets/"):
            continue
        p = Head()
        p.feed(f.read_text(encoding="utf-8"))
        pages[rel] = p
    return {"folder": folder, "url": site_url, "pages": pages,
            "indexable": {rel: p for rel, p in pages.items() if p.canonical}}


def test_homepage_title_and_social_titles(site):
    home = site["pages"]["index.html"]
    assert home.title == HOME_TITLE
    assert home.one("og:title") == HOME_TITLE
    assert home.one("twitter:title") == HOME_TITLE
    assert [h.rstrip("¶").strip() for h in home.h1] == ["Every image. Mach speed. Zero lost."]   # brand headline stays


def test_titles_are_unique_and_the_brand_is_not_doubled(site):
    titles = {rel: p.title.strip() for rel, p in site["indexable"].items()}
    assert all(titles.values()), [rel for rel, t in titles.items() if not t]
    for t in titles.values():
        assert "Machyon - Machyon" not in t and "Machyon · Machyon" not in t, t
    seen: dict[str, str] = {}
    for rel, t in titles.items():
        assert t not in seen, f"{rel} and {seen.get(t)} share the title {t!r}"
        seen[t] = rel


def test_404_page_has_a_title(site):
    p = site["pages"]["404.html"]
    assert p.title == "Page not found - Machyon"
    assert p.one("og:title") == p.title
    assert p.canonical is None and not p.jsonld


def test_social_tags_match_the_page(site):
    card = site["url"] + "assets/brand/social-card.png"
    for rel, p in site["pages"].items():
        assert p.one("og:title") == p.title, rel
        assert p.one("twitter:title") == p.title, rel
        assert p.one("og:description") == p.one("description"), rel
        assert p.one("twitter:description") == p.one("description"), rel
        assert p.one("og:image") == card and p.one("twitter:image") == card, rel
        assert (p.one("og:image:width"), p.one("og:image:height")) == ("1200", "630"), rel
        if p.canonical:
            assert p.one("og:url") == p.canonical, rel


def test_descriptions_are_unique_and_a_sensible_length(site):
    """50-160 characters is the editing guideline (long ones get cut off in results), not a search-engine rule."""
    seen: dict[str, str] = {}
    for rel, p in site["indexable"].items():
        d = p.one("description")
        assert 50 <= len(d) <= 160, f"{rel}: {len(d)} characters"
        assert d not in seen, f"{rel} and {seen.get(d)} share a description"
        seen[d] = rel


def test_search_console_verification_tag(site):
    html = (site["folder"] / "index.html").read_text(encoding="utf-8")
    assert html.count(VERIFICATION) == 1
    assert html.index(VERIFICATION) < html.index("</head>")
    assert site["pages"]["index.html"].meta["google-site-verification"] == [
        "eRLpDHbFuvuFv1DbACJxBL-dVZzBtzqf5azoMKgXZSM"]


def test_canonical_urls(site):
    for rel, p in site["indexable"].items():
        path = "" if rel == "index.html" else rel.removesuffix("index.html")
        assert p.canonical == site["url"] + path, rel


def test_sitemap_lists_every_indexable_page(site):
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    tree = ET.parse(site["folder"] / "sitemap.xml")           # raises on invalid XML
    locs = [e.text for e in tree.findall("s:url/s:loc", ns)]
    assert len(locs) == len(set(locs))
    assert all(u.startswith(site["url"]) and u.startswith("https://") for u in locs)
    assert set(locs) == {p.canonical for p in site["indexable"].values()}


def test_breadcrumbs(site):
    locs = {p.canonical for p in site["indexable"].values()}
    assert all("BreadcrumbList" not in b for b in site["pages"]["index.html"].jsonld)   # no crumbs on the homepage
    for rel, p in site["indexable"].items():
        if rel == "index.html":
            continue
        assert len(p.jsonld) == 1, rel
        ld = json.loads(p.jsonld[0])
        assert ld["@context"] == "https://schema.org" and ld["@type"] == "BreadcrumbList", rel
        items = ld["itemListElement"]
        assert len(items) >= 2, rel
        assert [i["position"] for i in items] == list(range(1, len(items) + 1)), rel
        assert all(i["@type"] == "ListItem" and i["name"].strip() for i in items), rel
        assert items[0]["item"] == site["url"] and items[-1]["item"] == p.canonical, rel
        assert all(i["item"] in locs for i in items), rel          # every crumb is a real page
    resuming = json.loads(site["pages"]["reliability/resuming/index.html"].jsonld[0])
    assert [i["name"] for i in resuming["itemListElement"]] == ["Machyon", "Reliability", "Resuming"]


def test_build_dependencies_are_not_published(site):
    assert not (site["folder"] / "requirements.txt").exists()
    assert (ROOT / "docs" / "requirements.txt").is_file()


def test_package_metadata():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["urls"]["Homepage"] == "https://divyanshusingh96.github.io/machyon/"
    assert project["urls"]["Repository"] == "https://github.com/DivyanshuSingh96/machyon"
    keywords = {k.lower() for k in project["keywords"]}
    assert not keywords & {"web scraping", "crawler", "scraper"}     # Machyon downloads URL lists; it doesn't crawl
    assert "image downloader" in keywords
    assert "Environment :: Console" in project["classifiers"]


# ---------------------------------------------------------------------------------------- performance and accessibility

WIDGET_SCRIPTS = ("engine-sim.js", "recording.js", "adaptive-lab.js", "opa-404.js")


class Body(HTMLParser):
    """Scripts, the header logo and the headings inside the page's article."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.logo: dict | None = None
        self.headings: list[tuple[int, str]] = []
        self._in_logo = 0
        self._article = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"].split("/")[-1])
        elif tag == "a" and "md-logo" in (a.get("class") or "") and "md-header__button" in (a.get("class") or ""):
            self._in_logo = 1
        elif tag == "img" and self._in_logo and self.logo is None:
            self.logo = a
        elif tag == "article":
            self._article += 1
        elif self._article and len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self.headings.append((int(tag[1]), a.get("class") or ""))

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_logo = 0
        elif tag == "article":
            self._article -= 1


@pytest.fixture(scope="module")
def bodies(site) -> dict:
    out = {}
    for rel in site["pages"]:
        b = Body()
        b.feed((site["folder"] / rel).read_text(encoding="utf-8"))
        out[rel] = b
    return out


def test_widget_scripts_load_only_where_used(site, bodies):
    """No page links the Lab's, the simulator's or the 404 page's scripts; widgets.js fetches them where the markup is."""
    loader = (site["folder"] / "assets/js/widgets.js").read_text(encoding="utf-8")
    for marker in ("data-mx-sim", "data-lab-replay", "data-lab-model", "data-opa-404"):
        assert marker in loader
    for rel, b in bodies.items():
        assert not set(b.scripts) & set(WIDGET_SCRIPTS), (rel, b.scripts)
        assert "widgets.js" in b.scripts, rel
    for name in ("engine-sim.js", "lab/recording.js", "lab/adaptive-lab.js", "opa-404.js"):
        assert (site["folder"] / "assets/js" / name).is_file(), name


def test_header_logo_has_its_proportions(site, bodies):
    svg = (ROOT / "docs/assets/brand/machyon-lockup-light.svg").read_text(encoding="utf-8")
    vb = [float(x) for x in svg.split('viewBox="', 1)[1].split('"', 1)[0].split()]
    for rel, b in bodies.items():
        assert b.logo and b.logo.get("width") and b.logo.get("height"), rel
        ratio = int(b.logo["width"]) / int(b.logo["height"])
        assert abs(ratio / (vb[2] / vb[3]) - 1) < 0.005, (rel, ratio)


def test_headings_never_skip_a_level(site, bodies):
    """The benchmark bars' labels used to be <h4> straight after an <h2>; no page skips a level now."""
    for rel, b in bodies.items():
        levels = [lv for lv, _ in b.headings]
        for prev, cur in zip(levels, levels[1:], strict=False):
            assert cur <= prev + 1, (rel, levels)
    html = (site["folder"] / "index.html").read_text(encoding="utf-8")
    bars = html.split('class="mx-bars"', 1)[1].split("</figure>", 1)[0]
    assert 'class="mx-bar-title"' in bars and "<h4" not in bars


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_muted_text_meets_wcag_aa():
    """The muted colours used for small text (the footer's Previous/Next, the Lab's instrument units) on their
    backgrounds, in both colour schemes, at full opacity: at least 4.5:1."""
    import re
    css = (ROOT / "docs/assets/css/machyon.css").read_text(encoding="utf-8")
    def schemes(name: str) -> list[str]:
        return re.findall(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", css)
    pairs = [("mx-muted", "mx-paper"), ("mx-muted", "mx-bg1"), ("lab-muted", "lab-paper")]
    for fg, bg in pairs:
        fgs, bgs = schemes(fg), schemes(bg)
        assert len(fgs) == len(bgs) == 2, (fg, bg)                   # light and dark
        for f, b in zip(fgs, bgs, strict=True):
            assert contrast(f, b) >= 4.5, (fg, f, bg, b, round(contrast(f, b), 2))
    assert re.search(r"\.md-footer__direction\s*\{[^}]*opacity:\s*1", css)          # not the theme's 0.7
    assert re.search(r"\.mx-lab-inst-num small\s*\{[^}]*opacity:\s*1", css)         # not the theme's faded <small>


def test_lists_did_not_run_into_the_paragraph_above(site):
    """GitHub renders a list straight after a line of text; this site needs a blank line, or the items merge."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "tools" / "check_docs.py")
    check_docs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_docs)
    merged = {}
    for rel in site["pages"]:
        page = check_docs.Page()
        page.feed((site["folder"] / rel).read_text(encoding="utf-8"))
        if page.run_on_lists:
            merged[rel] = page.run_on_lists
    assert not merged


def test_search_highlights_are_amber_and_cleared(site):
    """Search marks must not share the accent used by links, inline code and the current nav item."""
    config = tomllib.loads((ROOT / "zensical.toml").read_text(encoding="utf-8"))["project"]
    assert "assets/js/search-highlight.js" in config["extra_javascript"]
    assert (site["folder"] / "assets/js/search-highlight.js").is_file()
    css = (ROOT / "docs/assets/css/machyon.css").read_text(encoding="utf-8")
    mark = next(line for line in css.splitlines() if "--md-typeset-mark-color:" in line)
    assert "--mx-amber" in mark and "--mx-accent" not in mark


def test_homepage_structured_data(site):
    """The homepage describes the site, the software and its source code; the version is the package's."""
    head = site["pages"]["index.html"]
    graphs = [g for block in head.jsonld for g in json.loads(block).get("@graph", [])]
    by_type = {g["@type"]: g for g in graphs}
    assert {"WebSite", "SoftwareApplication", "SoftwareSourceCode", "Person"} <= set(by_type)
    app = by_type["SoftwareApplication"]
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert app["softwareVersion"] == version
    assert app["offers"]["price"] == "0" and "aggregateRating" not in app and "review" not in app
    assert by_type["SoftwareSourceCode"]["codeRepository"] == "https://github.com/DivyanshuSingh96/machyon"
    others = [rel for rel, p in site["pages"].items() if rel != "index.html"
              and any("@graph" in json.loads(b) for b in p.jsonld)]
    assert not others

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
    assert not site["pages"]["index.html"].jsonld
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

"""Build your first image dataset with Machyon: download, organise, check the report, browse a gallery.

    pip install machyon
    python build_dataset.py                                    # manifest.csv -> dataset/, then opens the gallery
    python build_dataset.py --manifest my.csv --out my-dataset --no-open

Walkthrough: https://divyanshusingh96.github.io/machyon/cookbook/first-dataset/
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import webbrowser
from pathlib import Path

import machyon

HERE = Path(__file__).resolve().parent
NAMES = "{category}/{id}_{name}{ext}"           # e.g. animals/1_cat.jpg


def build(manifest: Path, out: Path) -> machyon.Report:
    """Download every row of the manifest into out/images, then write out/report.json and out/gallery.html."""
    images = out / "images"

    # 1. The whole download is one call. Machyon reads the CSV, takes the URL from the "image_url" column, and
    #    names each file from the template using the row's own columns.
    report = machyon.download(manifest, images, url_col="image_url", names=NAMES)

    # 2. The report says what happened to every row: downloaded, already on disk (skipped), or failed.
    (out / "report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    # 3. Rows that failed are listed, with the reason, in the failure ledger next to the images.
    failures = read_failures(images)

    # 4. A gallery built from the files that are actually on disk.
    rows = read_rows(manifest)
    write_gallery(out / "gallery.html", rows, images, report, failures)
    return report


def read_rows(manifest: Path) -> list[dict]:
    with open(manifest, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_failures(images: Path) -> dict[str, dict]:
    """URL -> ledger entry, for every row that couldn't be downloaded."""
    ledger = images / "failed_downloads.jsonl"
    if not ledger.exists():
        return {}
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {e["url"]: e for e in entries}


def find_file(images: Path, row: dict) -> Path | None:
    """The file Machyon wrote for this row. {ext} is decided per file, so look for any extension."""
    matches = sorted((images / row["category"]).glob(f"{row['id']}_{row['name']}.*"))
    return matches[0] if matches else None


def write_gallery(path: Path, rows: list[dict], images: Path, report: machyon.Report, failures: dict) -> None:
    esc = html.escape
    sections: dict[str, list[str]] = {}
    for row in rows:
        file = find_file(images, row)
        if file is None:
            continue
        src = file.relative_to(path.parent).as_posix()
        credit = ""
        if row.get("author") and row.get("source"):
            credit = (f'<br>Photo: <a href="{esc(row["source"])}">{esc(row["author"])}</a>'
                      f' ({esc(row.get("license", ""))})')
        sections.setdefault(row["category"], []).append(
            f'<figure><img src="{esc(src)}" alt="{esc(row["name"])}" loading="lazy">'
            f'<figcaption><b>{esc(row["name"])}</b> <code>{esc(src)}</code>{credit}</figcaption></figure>')
    failed_rows = "".join(
        f'<tr><td>{esc(row["id"])}</td><td>{esc(row["name"])}</td><td><code>{esc(failures[row["image_url"]]["reason"])}'
        f'</code></td><td>{esc(failures[row["image_url"]].get("error", ""))}</td></tr>'
        for row in rows if row["image_url"] in failures)
    body = "".join(f'<h2>{esc(cat)} <small>{len(figs)}</small></h2><div class="grid">{"".join(figs)}</div>'
                   for cat, figs in sections.items())
    failed = (f'<h2>Couldn\'t be downloaded <small>{report.failed}</small></h2><table><tr><th>id</th><th>name</th>'
              f'<th>reason</th><th>details</th></tr>{failed_rows}</table>') if failed_rows else ""
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>My first image dataset</title>
<style>
  :root {{ color-scheme: light dark; --ink: #2B3C52; --muted: #4F637D; --paper: #FFFBF0; --card: #fff; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --ink: #ECF1FF; --muted: #AEB9DD; --paper: #2A3142; --card: #323A4F; }} }}
  body {{ margin: 0; font: 16px/1.55 system-ui, sans-serif; color: var(--ink); background: var(--paper); }}
  main {{ max-width: 70rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }}
  h1 {{ margin: 0 0 .3rem; }} h2 {{ margin: 2rem 0 .6rem; text-transform: capitalize; }}
  h2 small {{ color: var(--muted); font-weight: 400; font-size: .8em; }}
  .summary {{ color: var(--muted); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: 1rem; }}
  figure {{ margin: 0; background: var(--card); border-radius: 10px; overflow: hidden; box-shadow: 0 4px 18px rgba(0,0,0,.08); }}
  img {{ display: block; width: 100%; aspect-ratio: 3 / 2; object-fit: cover; }}
  figcaption {{ padding: .5rem .7rem .7rem; font-size: .8rem; color: var(--muted); }}
  figcaption b {{ color: var(--ink); }} a {{ color: inherit; }}
  code {{ font-size: .85em; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
  th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid rgba(127,127,127,.3); }}
</style></head>
<body><main>
<h1>My first image dataset</h1>
<p class="summary">Downloaded with Machyon {esc(machyon.__version__)}: {report.downloaded} downloaded,
{report.skipped} already on disk, {report.failed} couldn't be downloaded, in {report.duration:.1f} s.</p>
{body}
{failed}
</main></body></html>
""", encoding="utf-8")


def main(argv: list[str] | None = None) -> machyon.Report:
    ap = argparse.ArgumentParser(description="Build a small image dataset and a gallery to browse it.")
    ap.add_argument("--manifest", type=Path, default=HERE / "manifest.csv")
    ap.add_argument("--out", type=Path, default=Path("dataset"))
    ap.add_argument("--no-open", action="store_true", help="don't open the gallery in the browser")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)

    report = build(a.manifest, a.out)
    print(report)
    print(f"Images:  {(a.out / 'images').resolve()}")
    print(f"Report:  {(a.out / 'report.json').resolve()}")
    print(f"Gallery: {(a.out / 'gallery.html').resolve()}")
    if not a.no_open:
        webbrowser.open((a.out / "gallery.html").resolve().as_uri())
    return report


if __name__ == "__main__":
    main()

"""Machyon in 30 seconds."""
import machyon

# 1. A list of URLs -> a folder. Names default to "{hash}{ext}" (unique, stable, resumable).
urls = [
    "https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png",
]
report = machyon.download(urls, "images/quickstart")
print(report)            # "2 downloaded, 0 already on disk, 0 failed in 0.8s (2.4 img/s, ...)"

# 2. A CSV (or TSV / TXT / JSON / JSONL / Parquet). The URL column is detected automatically.
#    Name files from any column; {ext} becomes the real format when the URL has none.
# report = machyon.download("products.csv", "images/products", names="{sku}_{index}{ext}")

# 3. Run it again: finished files are skipped, so an interrupted run simply resumes.
print(machyon.download(urls, "images/quickstart"))   # "0 downloaded, 2 already on disk, ..."

# 4. What failed and why (also saved to images/quickstart/failed_downloads.jsonl)
print(report.failure_reasons)  # e.g. {"http_404": 3, "not_image": 1}

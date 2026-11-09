"""The dataset that started it all: the Amazon ML Challenge.

    python amazon_ml_challenge.py              # dataset/train.csv and dataset/test.csv -> images/

Files get the starter kit's names (the last part of each URL, such as 71XfHPR36-L.jpg), so notebooks
written for the starter kit's download_images() keep working. Works for every year's dataset: only the
`image_link` column is needed. In a notebook, use `await machyon.adownload(...)` with the same arguments.
Full recipe: https://divyanshusingh96.github.io/machyon/getting-started/amazon-ml-challenge/
"""
import machyon

for split in ("train", "test"):
    report = machyon.download(f"dataset/{split}.csv", f"images/{split}", url_col="image_link", names="{stem}{ext}")
    print(f"{split}: {report}")
    if not report.ok:
        print(f"  missing images are listed in images/{split}/failed_downloads.jsonl; "
              f"retry transient ones later with: machyon retry -o images/{split}")

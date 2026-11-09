"""Runs img2dataset (in its own venv) on the harness rows, given its best settings for raw download:
no resizing, no re-encoding, no EXIF/hash work. Then moves files to the harness names for verification.
usage: run_i2d.py rows.json save_dir work [retries] [threads] [processes]
"""
import csv
import json
import os
import shutil
import sys
import time

rows_path, save_dir, work = sys.argv[1:4]
retries = int(sys.argv[4]) if len(sys.argv) > 4 else 0
threads = int(sys.argv[5]) if len(sys.argv) > 5 else 256
procs = int(sys.argv[6]) if len(sys.argv) > 6 else 2
rows = json.load(open(rows_path))
inp = os.path.join(work, "urls.csv")
with open(inp, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["url", "name"])
    for _, url, name in rows:
        w.writerow([url, name])

if __name__ == "__main__":
    from img2dataset import download

    out = os.path.join(work, "i2d_out")
    t0 = time.time()
    download(url_list=inp, input_format="csv", url_col="url", save_additional_columns=["name"],
             output_folder=out, output_format="files", processes_count=procs, thread_count=threads,
             resize_mode="no", disable_all_reencoding=True, extract_exif=False, compute_hash=None,
             retries=retries, timeout=10, number_sample_per_shard=100000, enable_wandb=False)
    elapsed = time.time() - t0
    os.makedirs(save_dir, exist_ok=True)
    moved = 0
    for root, _, files in os.walk(out):
        for fn in files:
            if fn.endswith(".json") and not fn.endswith("_stats.json"):
                meta = json.load(open(os.path.join(root, fn)))
                img = os.path.join(root, fn[:-5] + ".jpg")
                if meta.get("status") == "success" and os.path.exists(img):
                    shutil.move(img, os.path.join(save_dir, meta["name"]))
                    moved += 1
    print("RESULT " + json.dumps({"elapsed": round(elapsed, 2), "moved": moved}))
